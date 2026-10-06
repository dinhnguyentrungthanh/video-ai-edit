/* Presenter. Data comes from one store: BFDemoStore (index.html, fixtures in memory)
 * or the live store of BFAdapter (live.html at /dashboard-v2). Only adapter.js talks HTTP. */
(function () {
'use strict';
const C=window.BFContracts, Mock=window.BFMock;
const LIVE=document.documentElement.dataset.mode==='live';
const store=LIVE?window.BFAdapter.createLiveStore(window.BFAdapter.create({contracts:C})):window.BFDemoStore.create(C,Mock);
let state=store.snapshot(), view='overview', filter='all', query='', sort='recent', page=1, selected=new Set(), currentJob=null;
let drawerFocus=null, modalFocus=null, modalCommit=null, modalBusy=false, toastTimer, scanFormJob=null;
const scanDrafts=new Map(), exportDrafts=new Map();
let aiDirty=false; /* unsaved AI settings: polling does not rebuild that form */
/* A draft belongs to one revision of one source; a new revision or source drops it. */
const draftKey=j=>j.job_key+'|'+(j.source_sha256||'')+'|'+j.active_revision;
const sent=label=>(LIVE?'Đã gửi: ':'Đã mô phỏng: ')+label;
const D=window.BFDownload;
let downloadDraft={url:'',error:''},downloadQueue=D.createQueue(),downloadFilter='all',downloadTimer=null;
const $=s=>document.querySelector(s), esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
/* Live "Tải video" (download-live.js) keeps its own page state; the demo keeps the simulation below. */
const DL=LIVE&&window.BFDownloadLive?window.BFDownloadLive.create({store,view:window.BFDownloadView,core:window.BFDownloadCore,$,icon:id=>icon(id),toast:(message,error)=>toast(message,error),showModal:(...args)=>showModal(...args),openCleanable:()=>changeFilter('completed')}):null;
const icons={
overview:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
videos:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3z"/>',
queue:'<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3" cy="6" r="1"/><circle cx="3" cy="12" r="1"/><circle cx="3" cy="18" r="1"/>',
logos:'<path d="m12 3 8 4v6c0 5-8 9-8 9s-8-4-8-9V7z"/><path d="m8 12 3 3 5-6"/>',
settings:'<path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.5 5.5l2 2m9 9 2 2m-13 0 2-2m9-9 2-2"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
check:'<path d="m5 12 4 4L19 6"/>',scan:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5M6 12h12"/>',
export:'<path d="M12 16V3m-5 5 5-5 5 5M4 14v6h16v-6"/>',review:'<path d="M5 4h14v17H5zM9 8h6M9 12h6M9 16h3"/>',
search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
folder:'<path d="M3 7V4h6l2 3h10v13H3z"/>'
};
icons.moon='<path d="M20 15.5A9 9 0 0 1 8.5 4a9 9 0 1 0 11.5 11.5Z"/>';
icons.sun='<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>';
icons.downloads='<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>';
let theme='light';
try{if(localStorage.getItem('biliflow-v2-theme')==='dark')theme='dark';}catch(_){/* A blocked preference store does not block the UI. */}
function applyTheme(){
  document.documentElement.dataset.theme=theme;
  const next=theme==='light'?'tối':'sáng',toggle=$('#theme-toggle');
  toggle.innerHTML=icon(theme==='light'?'moon':'sun')+'<span>'+(theme==='light'?'Tối':'Sáng')+'</span>';
  toggle.setAttribute('aria-label','Chuyển sang chế độ '+next);toggle.title='Chuyển sang chế độ '+next;
}
applyTheme();
function icon(id){return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(icons[id]||icons.videos)+'</svg>';}
function getJob(id){return state.jobs.find(j=>j.id===Number(id));}
function percent(v){return Math.min(100,Math.max(0,Math.round(100*(Number(v)||0))));}
function renderPercent(j){return Math.min(100,Math.max(0,Number(j.render_progress?.percent)||0));}
function bytes(n){return (n/1e9).toLocaleString('vi-VN',{maximumFractionDigits:2})+' GB';}
function reviewRemaining(j){return C.reviewStats(j).remaining;}
function ctx(){return {aiReady:state.ai.ready,fileBusy:state.source_cleanup_running,remote:!!state.remote};}
/* Phone listener: PC-only buttons stay visible but disabled with the backend's reason. */
function pcOnly(label){return state.remote?' disabled title="'+esc('Chỉ làm trên PC: '+label+' không làm qua điện thoại.')+'"':'';}
function ops(j){return C.operations(j,ctx()).map(a=>state.offline&&a.id!=='review'?{...a,enabled:false,reason:'Mất kết nối. Dữ liệu đang hiển thị là bản đã tải trước đó.'}:a);}
function stageLabel(stage){return ({visual_logo:'Nhận diện logo & watermark',advertising:'Kiểm tra logo & quảng cáo',adult:'Kiểm tra nội dung 18+',gore:'Kiểm tra máu me',violence:'Kiểm tra bạo lực',ocr:'Đọc chữ trong khung hình',VERIFYING:'Kiểm tra bản xuất'})[stage]||C.labels[stage]||'Đang phân tích cảnh';}
function drawerActions(j){
  const actions=ops(j),primary=C.primary(j);
  const featured=primary==='detail'?['stopAfter']:primary==='finalize'?['finalize','review']:primary==='reexport'?['reexport','review']:[primary];
  const main=featured.map(id=>actions.find(a=>a.id===id)).filter(Boolean),other=actions.filter(a=>!featured.includes(a.id));
  return '<h3>Thao tác chính</h3>'+(main.length?'<div class="drawer-primary-actions">'+main.map(a=>btn(j,a,'')).join('')+'</div>':'<p class="muted">Xem trạng thái và kết quả kiểm tra bên dưới.</p>')+(other.length?'<details class="more-actions"><summary>Thao tác khác</summary><div class="action-grid">'+other.map(a=>btn(j,a)).join('')+'</div></details>':'');
}
function drawerFocusable(el){
  for(let parent=el.parentElement;parent&&!parent.classList.contains('drawer');parent=parent.parentElement){
    if(parent.tagName==='DETAILS'&&!parent.open&&el!==parent.querySelector(':scope > summary'))return false;
  }
  return el.getClientRects().length>0;
}
function badge(j){
  const t=C.tab(j),tone=j.state==='FAILED'?'red':j.state==='WAITING_REVIEW'?'amber':['scanning','export'].includes(t)?'blue':['COMPLETED','READY_TO_EXPORT'].includes(j.state)?'':'grey';
  const text=j.state==='QUEUED'?j.queue_kind==='export'?'Chờ xuất':j.queue_kind==='scan'?'Chờ quét':'Đang chờ':j.state==='RENDERING'&&j.render_progress?.state==='VERIFYING'?'Kiểm tra bản xuất':C.labels[j.state]||j.state;
  return '<span class="badge '+tone+'"><span class="live-dot" style="background:currentColor"></span>'+esc(text)+'</span>';
}
function btn(j,a,extra){return '<button '+(a.enabled?'':'disabled')+' class="'+(a.id==='finalize'||a.id==='start'?'primary':a.id==='cancel'?'danger':'secondary')+' '+(extra||'small')+'" data-action="job" data-id="'+j.id+'" data-op="'+a.id+'" title="'+esc(a.reason||a.label)+'">'+esc(a.label)+'</button>';}
function nav(){
  const labels={overview:'Tổng quan',downloads:'Tải video',videos:'Video của bạn',queue:'Hàng đợi',logos:'Bộ nhớ logo',settings:'Cài đặt'};
  $('#navigation').innerHTML=Object.entries(labels).map(([id,label])=>'<a class="nav-link '+(view===id?'active':'')+'" href="#'+id+'" '+(view===id?'aria-current="page"':'')+' aria-label="'+label+'" title="'+label+'">'+icon(id)+'<span>'+label+'</span>'+(id==='videos'?'<span class="nav-count">'+state.jobs.filter(j=>!C.hidden(j)).length+'</span>':'')+'</a>').join('');
  $('#breadcrumb').textContent=labels[view];
}
function scenarioOptions(){return [['normal','Hoạt động bình thường'],['rendering','Đang kiểm tra bản xuất'],['offline','Mất kết nối'],['bin_full','Thùng rác gần đầy'],['busy','Đang quản lý video gốc'],['conflict','Xung đột dữ liệu 409']].map(([id,label])=>'<option value="'+id+'" '+(state.scenario===id?'selected':'')+'>'+label+'</option>').join('');}
function heading(title,subtitle,actions){return '<div class="page-heading"><div><h1>'+title+'</h1><p>'+subtitle+'</p></div><div class="heading-actions">'+(actions||'<span class="live-status"><span class="live-dot"></span> '+(state.scheduler_paused?'Hàng đợi tạm dừng':'Một GPU · xử lý tuần tự')+'</span><button class="secondary small" data-action="scheduler" '+(state.offline?'disabled':'')+'>'+(state.scheduler_paused?'Tiếp tục hàng đợi':'Tạm dừng hàng đợi')+'</button>')+'</div></div>';}
function visibleJobs(){return state.jobs.filter(j=>!C.hidden(j));}
function kpis(){
  const js=visibleJobs(),need=js.filter(j=>j.state==='WAITING_REVIEW').length,remaining=js.reduce((n,j)=>n+(j.state==='WAITING_REVIEW'?reviewRemaining(j):0),0);
  const count=group=>js.filter(j=>C.overviewMatch(j,group)).length;
  const values=[['scan','Đang quét cảnh',count('scan_active'),'Nhận diện nội dung, tạo cảnh duyệt','blue','scan_active'],['review','Chờ bạn duyệt',need,remaining+' cảnh cần quyết định của bạn','warning','review_pending'],['export','Sẵn sàng xuất',count('review_ready'),'Đã duyệt xong, chưa gửi lệnh xuất','','review_ready'],['export','Đang xuất video',count('export_active'),'Tạo video hoặc kiểm tra bản xuất','blue','export_active'],['check','Hoàn tất',js.filter(j=>['COMPLETED','SKIPPED'].includes(j.state)).length,js.filter(j=>j.state==='COMPLETED').length+' đã xuất · '+js.filter(j=>j.state==='SKIPPED').length+' bỏ qua','','completed']];
  return '<div class="kpi-grid">'+values.map(([ico,label,n,note,tone,target])=>'<button class="kpi '+tone+'" data-action="summary-filter" data-filter="'+target+'" aria-pressed="'+(filter===target)+'"><div class="kpi-top"><span>'+label+'</span><span class="kpi-icon">'+icon(ico)+'</span></div><div class="kpi-number">'+n+'<small>video</small></div><div class="kpi-note">'+note+'</div></button>').join('')+'</div><p class="overview-legend">Các ô đếm theo video đang ở từng bước. Đang chờ đến lượt: <strong>'+js.filter(j=>j.state==='QUEUED'&&j.queue_kind==='scan').length+' chờ quét</strong> · <strong>'+js.filter(j=>j.state==='QUEUED'&&j.queue_kind==='export').length+' chờ xuất</strong>.</p>';
}
function hero(){
  const j=getJob(state.active?.job_id),r=state.resources,exporting=j&&C.tab(j)==='export';
  const verifying=exporting&&(j.state==='VERIFYING'||j.render_progress?.state==='VERIFYING');
  const progress=exporting?renderPercent(j):percent(j?.progress);
  const step=j?(exporting?verifying?'Kiểm tra bản xuất':'Đang tạo video đã duyệt':stageLabel(j.current_stage||j.state)):'Không có bước đang chạy';
  const waiting=visibleJobs().filter(x=>x.state==='QUEUED');
  const queueNote=waiting.filter(x=>x.queue_kind==='scan').length+' chờ quét · '+waiting.filter(x=>x.queue_kind==='export').length+' chờ xuất';
  return '<div class="hero-grid"><section class="active-card"><div class="eyebrow"><span class="live-dot"></span>'+(j?exporting?'ĐANG XUẤT VIDEO':'ĐANG QUÉT CẢNH':'WORKER ĐANG CHỜ')+'</div><h2>'+esc(j?.name||'Sẵn sàng cho video tiếp theo')+'</h2><p>'+esc(j?({animation:'Hoạt hình',live_action:'Phim người thật',mixed:'Nội dung hỗn hợp'})[j.content_style]+' · '+j.duration+' · '+(exporting?'Xuất theo các cảnh đã duyệt':j.detector_groups.length+' nhóm kiểm tra'):'Video trong hàng đợi sẽ giữ nguyên thứ tự bấm.')+'</p><div class="hero-status"><span>'+esc(step)+'</span><strong>'+progress+'%</strong></div><div class="meter"><i style="width:'+progress+'%"></i></div><div class="active-foot"><span>'+esc((j?'#'+j.id+' · ':'')+queueNote)+'</span>'+(j?'<button class="small" data-action="detail" data-id="'+j.id+'">Xem tiến trình '+icon('arrow')+'</button>':'')+'</div></section><section class="resources-card"><div class="section-top"><h2>Tài nguyên máy</h2><span class="live-dot"></span></div>'+[['cpu','CPU',r.cpu_percent],['ram','RAM',r.memory.percent],['gpu','GPU',r.gpu?r.gpu.utilization_percent:null]].map(([cls,label,n])=>'<div class="resource-row '+cls+'"><span>'+label+'</span><div class="meter"><i style="width:'+(Number(n)||0)+'%"></i></div><strong>'+(n==null?'N/A':Math.round(n)+'%')+'</strong></div>').join('')+'<div class="resource-info"><span>'+esc(r.gpu?(r.gpu.name||'GPU'):'GPU · N/A')+'</span><span>'+(r.gpu&&Number.isFinite(r.gpu.memory_total_bytes)?'VRAM '+bytes(r.gpu.memory_used_bytes||0)+' / '+bytes(r.gpu.memory_total_bytes):'VRAM N/A')+'</span></div></section></div>';
}
function scope(j){return '<div class="scope-chips">'+Object.entries(C.detectors).map(([id,label])=>'<span class="scope-chip '+(j.detector_groups.includes(id)?'':'off')+'" title="'+esc(j.detector_groups.includes(id)?label+' đã chọn':label+' chưa kiểm tra')+'">'+({advertising:'Logo',adult:'18+',gore:'Máu me',violence:'Bạo lực'})[id]+'</span>').join('')+'</div>';}
/* U3: in "Hoàn tất" every row has a selection box. Only a video the server lets clean or archive (C.eligible) can be
   ticked; any other box is disabled with the server's reason (cleanup.reason, plus archive.reason when it differs).
   Through the phone (2026-10-06) only "Xóa video gốc" picks: "Lưu trữ" stays disabled with C.PC_ONLY_REASON. */
const SELECT_NONE='Không có video nào xóa video gốc hoặc lưu trữ được',SELECT_NONE_PHONE='Không có video nào xóa video gốc được',SELECT_PC_ONLY='Lưu trữ chỉ làm trên PC';
/* A golden-set video (j.protected) is never deleted: it can only be picked for "Lưu trữ". */
function cleanable(j){return C.eligible(j,'cleanup')&&!j.protected;}
function selectable(j){return cleanable(j)||!state.remote&&C.eligible(j,'archive');}
function selectReasons(j){
  const local=C.archived(j)?'Video gốc đã được lưu trữ':C.cleaned(j)?'Video gốc đã được dọn trước đó':j.source_present===false?'Video gốc không còn trong thư mục input':C.inFlight(j)?'Còn lệnh xuất video chưa xong':'Chưa xóa video gốc hay lưu trữ được video này';
  const c=String(j.protected||j.cleanup?.reason||''),a=String(j.archive?.reason||''),first=c||a||local;
  return [first,a&&a!==first?a:''];
}
function selectReason(j){const [c,a]=selectReasons(j),why=a?'Xóa video gốc: '+c+' · Lưu trữ: '+a:c;return state.remote&&C.eligible(j,'archive')?why+' · '+SELECT_PC_ONLY:why;}
function reasonShort(r){return r==='Video gốc đã được dọn trước đó'?'đã dọn':r.startsWith('Không thấy bản xuất')?'thiếu bản xuất':r==='Video gốc không còn trong thư mục input'?'không còn video gốc':r==='Video gốc đã được lưu trữ'?'đã lưu trữ':r;}
function reasonCounts(jobs){
  const counts=new Map();jobs.forEach(j=>{const k=reasonShort(selectReasons(j)[0]);counts.set(k,(counts.get(k)||0)+1);});
  return [...counts].sort((x,y)=>y[1]-x[1]).map(([k,n])=>n+' '+k).join(' · ');
}
function selectBox(j){
  if(!selectable(j)){const r=selectReason(j);return '<input class="select-job" type="checkbox" disabled title="'+esc(r)+'" aria-label="'+esc('Không chọn được video '+j.id+': '+r)+'">';}
  return '<input class="select-job" type="checkbox" aria-label="Chọn video '+j.id+'" data-select="'+j.id+'" '+(selected.has(j.id)?'checked':'')+(state.source_cleanup_running||state.offline?' disabled':'')+'>';
}
function row(j){
  // R4-U1: a video waiting for or in its export shows only ⋯ (its actions stay in the drawer).
  const isExport=C.tab(j)==='export',main=isExport?null:ops(j).find(a=>a.id===C.primary(j)),remaining=reviewRemaining(j),p=j.state==='RENDERING'?renderPercent(j):percent(j.progress);
  const progText=j.state==='VERIFYING'||j.render_progress?.state==='VERIFYING'?'Kiểm tra':isExport&&j.state==='QUEUED'?'Chờ xuất':p+'%';
  const src=C.sourceLine(j);
  const note=src&&src[1]==='error'?src[0]:C.archived(j)?'Đã lưu trữ video gốc':C.cleaned(j)?'Video gốc trong Thùng rác':j.state==='WAITING_REVIEW'?remaining+' cảnh cần duyệt':j.queue_position?'Lượt #'+j.queue_position:'Revision '+j.active_revision;
  const why=filter==='completed'&&!selectable(j)?selectReason(j):'';
  const whyLine=why&&!note.includes(why)?'<span class="cell-sub select-reason">Không chọn được: '+esc(why)+'</span>':'';
  return '<article class="job-row" data-job="'+j.id+'"><div class="video-cell">'+(filter==='completed'?selectBox(j):'')+'<img class="poster" src="assets/poster-'+j.palette+'.svg" alt="" loading="lazy"><div class="video-text"><button class="video-title" data-action="detail" data-id="'+j.id+'" title="'+esc(j.name)+'">'+esc(j.name)+'</button><div class="video-meta"><span>#'+j.id+'</span><span>·</span><span>'+j.duration+'</span><span>·</span><span>'+bytes(j.source_size_bytes)+'</span></div></div></div><div class="status-cell">'+badge(j)+'<span class="cell-sub'+(src&&src[1]==='error'?' tone-error':'')+'">'+esc(note)+'</span>'+whyLine+'</div><div class="scope-cell">'+scope(j)+'</div><div class="progress-cell"><div class="row-progress"><span>'+progText+'</span><div class="meter"><i style="width:'+p+'%"></i></div></div></div><div class="row-actions">'+(isExport?'':main?btn(j,{...main,label:main.id==='start'?'Thiết lập':main.id==='restore'?'Khôi phục':main.label},''): '<button class="secondary small" data-action="detail" data-id="'+j.id+'">Chi tiết</button>')+'<button class="icon-button" data-action="detail" data-id="'+j.id+'" aria-label="Thao tác video '+j.id+'" title="Thao tác video">⋯</button></div></article>';
}
function allFiltered(){
  const needle=query.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  return state.jobs.filter(j=>C.overviewLabels[filter]?C.overviewMatch(j,filter):(C.hidden(j)?filter==='all'||filter==='waiting':filter==='all'||C.tab(j)===filter)).filter(j=>(j.name+' '+j.id+' '+j.source_path).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().includes(needle)).sort((a,b)=>sort==='queue'?(a.queue_position||1e6)-(b.queue_position||1e6)||a.id-b.id:sort==='name'?a.name.localeCompare(b.name,'vi'):b.updated_at.localeCompare(a.updated_at)||a.id-b.id);
}
function bulkToolbar(all,ids){
  if(filter!=='completed')return '';
  const n=all.filter(selectable).length,busy=state.source_cleanup_running||state.offline,off=t=>' disabled title="'+esc(t)+'"';
  let label,pick='',clear='',clean,archive;
  if(!n){const counts=reasonCounts(all),none=state.remote?SELECT_NONE_PHONE:SELECT_NONE;label=none+(counts?'<small class="bulk-reasons"> · '+esc(counts)+'</small>':'');pick=clear=off(none);clean=archive=' disabled';}
  else{label=selected.size+' video đã chọn · '+n+' video chọn được';clean=!ids.some(id=>cleanable(getJob(id)))||busy?' disabled':'';archive=!ids.some(id=>C.eligible(getJob(id),'archive'))||busy?' disabled':'';}
  if(state.remote)archive=off(C.PC_ONLY_REASON);
  return '<div class="bulk-toolbar"><span class="bulk-label">'+label+'</span><button class="small secondary" data-action="select-all"'+pick+'>Chọn tối đa 50</button><button class="small secondary" data-action="deselect"'+clear+'>Bỏ chọn</button><button class="small" data-action="bulk-cleanup"'+clean+'>Xóa video gốc</button><button class="small" data-action="bulk-archive"'+archive+'>Lưu trữ</button></div>';
}
function listBody(){
  const all=allFiltered(),folds=[['Đã hủy',all.filter(j=>j.state==='CANCELLED'&&!C.hidden(j)),'cancelled'],['Đã ẩn',all.filter(C.hidden),'hidden'],['Đã lưu trữ',all.filter(j=>C.archived(j)),'archived']];
  const regular=all.filter(j=>j.state!=='CANCELLED'&&!C.archived(j)),pages=Math.max(1,Math.ceil(regular.length/8));page=Math.min(page,pages);
  const ids=[...selected].filter(id=>selectable(getJob(id)));selected=new Set(ids);
  return bulkToolbar(all,ids)+'<div class="jobs-head"><span>VIDEO</span><span>TRẠNG THÁI</span><span>PHẠM VI QUÉT</span><span>TIẾN ĐỘ</span><span style="text-align:right">THAO TÁC</span></div>'+regular.slice((page-1)*8,page*8).map(row).join('')+(all.length?'':'<div class="empty">'+icon('folder')+'<strong>Không có video phù hợp</strong>Thử đổi bộ lọc hoặc từ khóa tìm kiếm.</div>')+folds.filter(x=>x[1].length).map(([label,items,key])=>'<details class="fold" data-fold="'+key+'"><summary>'+label+' ('+items.length+')</summary>'+items.map(row).join('')+'</details>').join('')+'<div class="list-footer"><span>'+all.length+' video phù hợp · '+visibleJobs().length+' video trong không gian</span><div class="pages"><button data-action="prev" '+(page===1?'disabled':'')+' aria-label="Trang trước">‹</button><span>'+page+' / '+pages+'</span><button data-action="next" '+(page===pages?'disabled':'')+' aria-label="Trang sau">›</button></div></div>';
}
/* "Dọn video mất gốc": every video whose source is gone (not archived) leaves BiliFlow in one dialog; output stays. */
function lostNotice(){
  const ids=C.lostIds(state.jobs);if(!ids.length)return '';
  const off=state.source_cleanup_running||state.offline?' disabled':'';
  return '<div class="notice lost-notice">Có '+ids.length+' video không còn video gốc · <button class="small" data-action="purge-lost"'+off+'>Dọn video mất gốc</button>'+(ids.length>50?' <small>Mỗi lần tối đa 50 video.</small>':'')+'</div>';
}
function list(){
  const visible=visibleJobs();
  return '<section class="list-section" id="video-list"><div class="list-title"><h2>Video của bạn</h2><small>'+visible.length+'</small><span class="list-subtitle">Từ input đến bản xuất, trong một không gian</span></div>'+lostNotice()+'<div class="list-tools">'+(C.overviewLabels[filter]?'<div class="focused-filter"><span>Đang lọc: <strong>'+esc(C.overviewLabels[filter])+'</strong> · cùng nhóm với ô tổng quan bạn vừa chọn</span><button class="small secondary" data-action="filter" data-filter="all">Xem tất cả</button></div>':'')+'<div class="filter-tabs" role="group" aria-label="Lọc theo giai đoạn">'+C.tabs.map(([id,label])=>'<button class="filter-tab '+(filter===id?'active':'')+'" data-action="filter" data-filter="'+id+'" aria-pressed="'+(filter===id)+'">'+label+'<span>'+(id==='all'?visible.length:visible.filter(j=>C.tab(j)===id).length)+'</span></button>').join('')+'</div><div class="search-sort"><label class="search-wrap">'+icon('search')+'<input id="search" placeholder="Tìm tên video hoặc mã job…" aria-label="Tìm video" value="'+esc(query)+'"></label><select id="sort" aria-label="Sắp xếp video">'+[['recent','Mới cập nhật'],['queue','Thứ tự hàng đợi'],['name','Tên video A–Z']].map(([id,label])=>'<option value="'+id+'" '+(sort===id?'selected':'')+'>'+label+'</option>').join('')+'</select></div></div><div id="list-body">'+listBody()+'</div></section>';
}
function queueView(){
  const jobs=state.jobs.filter(j=>j.state==='QUEUED').sort((a,b)=>a.queue_position-b.queue_position);
  return heading('Hàng đợi','Quét cảnh và xuất video dùng chung một hàng đợi, theo thứ tự bạn bấm.')+'<div class="notice">Một GPU xử lý tuần tự. Tạm dừng hàng đợi giữ nguyên vị trí; tiếp tục hoặc thử lại cũng giữ vị trí đã có.</div>'+jobs.map(j=>'<div class="queue-row"><span class="queue-rank">'+j.queue_position+'</span><div><strong>'+esc(j.name)+'</strong><p>#'+j.id+' · '+(j.queue_kind==='export'?'Xuất video':'Quét cảnh')+' · '+(state.scheduler_paused?'Đang tạm dừng hàng đợi':'Chờ worker sẵn sàng')+'</p></div><button class="secondary small" data-action="detail" data-id="'+j.id+'">Chi tiết '+icon('arrow')+'</button></div>').join('')+(jobs.length?'':'<div class="empty"><strong>Hàng đợi đang trống</strong>Thiết lập video mới hoặc xuất video đã duyệt.</div>');
}
/* Live: only frame URLs the server listed for this record (same origin, logo-memory frame route). */
function logoFrames(l){
  if(!LIVE)return [0,1,2,3].map((_,i)=>'<img src="assets/poster-'+l.color+'.svg" alt="Ảnh minh họa mẫu '+(i+1)+'">').join('');
  const urls=(Array.isArray(l.frame_urls)?l.frame_urls:[]).filter(u=>typeof u==='string'&&u.startsWith('/api/logo-memory/frame?')).slice(0,4);
  return urls.length?urls.map((u,i)=>'<img loading="lazy" src="'+esc(u)+'" alt="Khung hình '+(i+1)+' của logo">').join(''):'<span class="muted">Không có khung hình</span>';
}
function logosView(){
  return heading('Bộ nhớ logo','Xem và quản lý logo đã nhớ. Các lần duyệt trước vẫn được giữ.','<span class="badge">'+state.logos.length+(LIVE?' bản ghi':' bản ghi mẫu')+'</span>')+'<div class="notice">Logo nền tảng → đề xuất làm mờ vùng logo. Logo hãng phim → đề xuất giữ nguyên. Bạn duyệt quyết định cuối cùng cho từng video.</div><div class="memory-grid">'+state.logos.map(l=>'<article class="memory-card"><div class="memory-art">'+esc(l.memory_class==='platform_logo'?l.name.split(' · ')[0]:'STUDIO')+'</div><div class="panel"><h3>'+esc(l.name)+'</h3><span class="badge '+(l.memory_class==='studio_logo'?'blue':'')+'">'+(l.memory_class==='studio_logo'?'Hãng phim · KEEP':'Nền tảng · BLUR')+'</span><div class="mini-frames">'+logoFrames(l)+'</div><p>'+l.frames+(LIVE?' khung hình':' khung hình mẫu')+' · thay đổi được sao lưu trước</p>'+(LIVE&&l.refusal_text?'<p class="muted">'+esc(l.refusal_text)+'</p>':'')+'<div class="action-grid"><button class="secondary small" data-action="logo-class" data-key="'+esc(l.key)+'" '+(state.remote?pcOnly('sửa bộ nhớ logo'):(state.offline||LIVE&&l.convertible===false?'disabled':'')+' title="'+esc(LIVE&&l.convertible===false?l.refusal_text||'Không đổi được loại':'Đổi loại')+'"')+'>Đổi loại</button><button class="danger small" data-action="logo-delete" data-key="'+esc(l.key)+'" '+(state.offline?'disabled':'')+pcOnly('xóa bộ nhớ logo')+'>Xóa khỏi bộ nhớ</button></div></div></article>').join('')+'</div>';
}
function downloadBusy(){return downloadQueue.items.some(t=>D.active(t)||t.state==='QUEUED');}
function downloadProgress(){
  const q=downloadQueue,s=D.stats(q),matches=t=>downloadFilter==='all'||downloadFilter==='active'&&D.active(t)||downloadFilter==='queued'&&t.state==='QUEUED'||downloadFilter==='completed'&&t.state==='COMPLETED'||downloadFilter==='failed'&&t.state==='FAILED';
  const items=q.items.filter(matches),counts={all:q.items.length,active:s.active,queued:s.queued,completed:s.completed,failed:s.failed};
  return '<div class="download-counts">'+[['active',q.paused?'Tạm dừng chung':'Đang tải / kiểm tra'],['queued','Chờ tải'],['completed','Hoàn tất'],['failed','Lỗi']].map(([key,label])=>'<div><strong>'+counts[key]+'</strong><span>'+label+'</span></div>').join('')+'</div><div class="download-queue-tools"><label>Tải đồng thời <select id="download-parallel" aria-label="Số lượt tải đồng thời">'+[1,2,3].map(n=>'<option value="'+n+'" '+(q.parallel===n?'selected':'')+'>'+n+' video</option>').join('')+'</select></label><button class="secondary small" data-action="download-toggle" '+(!q.items.some(t=>D.active(t)||t.state==='QUEUED')?'disabled':'')+'>'+(q.paused?'Tiếp tục tất cả':'Tạm dừng tất cả')+'</button></div>'+(q.paused?'<p class="download-paused">Đã tạm dừng toàn bộ tiến độ tải mẫu.</p>':'')+'<div class="download-filters" aria-label="Lọc lượt tải">'+[['all','Tất cả'],['active',q.paused?'Dừng chung':'Đang tải'],['queued','Chờ tải'],['completed','Hoàn tất'],['failed','Lỗi']].map(([key,label])=>'<button class="filter-tab '+(downloadFilter===key?'active':'')+'" data-action="download-filter" data-filter="'+key+'" aria-pressed="'+(downloadFilter===key)+'">'+label+' <span>'+counts[key]+'</span></button>').join('')+'</div><div class="download-list">'+(items.length?items.map(downloadRow).join(''):'<div class="download-empty">'+icon('downloads')+'<h3>'+(q.items.length?'Chưa có lượt tải trong nhóm này':'Sẵn sàng tải nhiều video')+'</h3><p>Mỗi video có tiến độ riêng.<br>Bạn có thể thêm link trong khi đang tải.</p></div>')+'</div><p class="download-list-foot">'+q.items.length+' lượt tải mẫu · '+s.paused+' tạm dừng riêng · '+q.items.filter(t=>t.state==='CANCELLED').length+' đã hủy</p>';
}
function downloadRow(t){
  const q=downloadQueue,active=D.active(t),paused=t.state==='PAUSED',done=t.state==='COMPLETED',failed=t.state==='FAILED';
  const label=q.paused&&active?'Tạm dừng tất cả':t.code==='UNSUPPORTED'?'Chưa hỗ trợ':({QUEUED:'Chờ tải',DOWNLOADING:'Đang tải',VERIFYING:'Kiểm tra tệp',COMPLETED:'Hoàn tất',PAUSED:'Tạm dừng',FAILED:'Lỗi tải',CANCELLED:'Đã hủy'})[t.state];
  const control=(op,label,disabled=false)=>'<button class="secondary small" data-action="download-item" data-id="'+t.id+'" data-op="'+op+'" aria-label="'+label+' lượt '+t.id+'" '+(disabled?'disabled':'')+'>'+label+'</button>';
  const place=t.state==='QUEUED'?q.items.filter(x=>x.state==='QUEUED').findIndex(x=>x.id===t.id)+1:null;
  return '<article class="download-item" data-download-id="'+t.id+'"><div class="download-item-top"><div class="download-item-name"><span class="download-item-number">'+t.id+'</span><h3>'+esc(t.name)+'</h3></div><span class="badge '+(failed?'red':done?'':active?'blue':'grey')+'">'+label+'</span></div><p class="download-source">'+esc(t.url)+'</p><div class="download-item-progress"><span>'+(place?'Lượt chờ #'+place:done?'Mô phỏng hoàn tất':t.state==='VERIFYING'?'Kiểm tra trước khi vào input':Math.round(t.progress*3.2)+' / 320 MB mẫu')+'</span><strong>'+t.progress+'%</strong></div><div class="meter download-meter" role="progressbar" aria-label="Tiến độ lượt '+t.id+'" aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+t.progress+'"><i style="width:'+t.progress+'%"></i></div>'+(failed?'<p class="download-item-error">'+esc(t.error)+'</p>':'')+'<div class="download-item-actions">'+(active?control('pause','Tạm dừng',q.paused):paused?control('resume','Tiếp tục',q.paused||q.items.filter(D.active).length>=q.parallel):'')+(['QUEUED','DOWNLOADING','VERIFYING','PAUSED'].includes(t.state)?control('cancel','Hủy'):['FAILED','CANCELLED'].includes(t.state)&&t.code!=='UNSUPPORTED'?control('retry','Thử lại'):'')+'</div><details class="download-log" data-log-id="'+t.id+'"><summary>Nhật ký lượt tải</summary><ol>'+t.logs.map(line=>'<li>'+esc(line)+'</li>').join('')+'</ol>'+(active?control('fail','Giả lập lỗi'):'')+'</details></article>';
}
function downloadsView(){
  if(DL)return heading('Tải video','Dán link, theo dõi từng lượt. File tải xong vào thư mục input để bạn quét như mọi video.','<span class="badge blue" id="dl-mode">'+(state.remote?'Qua điện thoại':'Control Center')+'</span>')+DL.html();
  const sample='https://video.example/watch?v=video-demo';
  return heading('Tải video','Thêm nhiều liên kết, theo dõi riêng từng video.','<span class="badge blue">'+(LIVE?'MÔ PHỎNG':'Demo giao diện')+'</span>')+(LIVE?'<div class="notice" role="note" id="download-simulation"><strong>Trang này là mô phỏng.</strong> Chưa có downloader hay API tải thật: không tải video, không gọi mạng, không ghi vào input. Tiến độ bên dưới chỉ là dữ liệu giả trong tab.</div>':'')+'<div class="download-layout"><section class="panel download-form"><div class="download-panel-title">'+icon('downloads')+'<h2>Thêm video</h2></div><p class="muted">Mỗi dòng một link, từ trang nào cũng được. Trang không có video đọc được sẽ báo "Chưa hỗ trợ". Có thể thêm lượt mới trong khi các video khác đang tải.</p><label class="field"><span>Liên kết video</span><textarea id="download-url" rows="5" aria-label="Liên kết video" autocomplete="off" spellcheck="false" placeholder="Mỗi dòng một link https://…" maxlength="41000" aria-describedby="download-hint download-error">'+esc(downloadDraft.url)+'</textarea><small id="download-hint">Link mẫu: <span class="mono">'+esc(sample)+'</span></small></label><p id="download-error" class="modal-error" role="alert" '+(downloadDraft.error?'':'hidden')+'>'+esc(downloadDraft.error)+'</p><button class="primary download-start" data-action="download-start" '+(state.offline?'disabled':'')+'>'+icon('downloads')+' Thêm vào danh sách tải</button><button class="secondary download-sample" data-action="download-sample" '+(state.offline?'disabled':'')+'>Thử với 3 video mẫu</button><div class="download-destination"><span>Thư mục lưu dự kiến</span><strong class="mono">E:\\DungChung\\BiliFlow\\input\\</strong></div><div class="download-demo-note"><strong>Bản mẫu để bạn thử giao diện</strong><p>Tiến độ và nhật ký được mô phỏng. Chưa chạy command/PowerShell, tải tệp thật hoặc tự quét cảnh.</p></div></section><section class="panel download-progress-panel" aria-labelledby="download-progress-title"><div class="section-top"><h2 id="download-progress-title">Danh sách & tiến độ tải</h2><span class="badge grey">Mô phỏng</span></div><div id="download-progress">'+downloadProgress()+'</div></section></div>';
}
function refreshDownload(){
  if(view!=='downloads')return;const root=$('#download-progress');if(!root)return;
  const open=[...root.querySelectorAll('details[open]')].map(el=>el.dataset.logId),focus=document.activeElement;
  const id=focus?.dataset.id,op=focus?.dataset.op,action=focus?.dataset.action,parallel=focus?.id==='download-parallel';
  const scroll=root.querySelector('.download-list')?.scrollTop||0,next=document.createElement('div');next.innerHTML=downloadProgress();
  for(const selector of ['.download-counts','.download-filters','.download-list','.download-list-foot'])root.querySelector(selector).innerHTML=next.querySelector(selector).innerHTML;
  const toggle=root.querySelector('[data-action="download-toggle"]'),nextToggle=next.querySelector('[data-action="download-toggle"]');toggle.textContent=nextToggle.textContent;toggle.disabled=nextToggle.disabled;
  const paused=root.querySelector('.download-paused');if(paused)paused.remove();if(downloadQueue.paused)root.querySelector('.download-queue-tools').insertAdjacentHTML('afterend',next.querySelector('.download-paused').outerHTML);
  root.querySelector('.download-list').scrollTop=scroll;open.forEach(id=>{const el=root.querySelector('[data-log-id="'+id+'"]');if(el)el.open=true;});
  const target=parallel?$('#download-parallel'):id&&op?root.querySelector('[data-id="'+id+'"][data-op="'+op+'"]'):action==='download-toggle'?root.querySelector('[data-action="download-toggle"]'):action==='download-filter'?root.querySelector('[data-action="download-filter"][data-filter="'+focus.dataset.filter+'"]'):null;
  if(target&&!target.disabled)target.focus({preventScroll:true});else if(id){const fallback=root.querySelector('[data-download-id="'+id+'"] summary');if(fallback)fallback.focus({preventScroll:true});}
}
function ensureDownloadTimer(){
  if(downloadTimer||!downloadBusy()||downloadQueue.paused)return;
  downloadTimer=setInterval(()=>{D.tick(downloadQueue);refreshDownload();if(!downloadBusy()||downloadQueue.paused){clearInterval(downloadTimer);downloadTimer=null;}},1000);
}
function startDownload(){
  if(state.offline)return;
  downloadDraft.url=$('#download-url').value;
  try{
    const count=D.enqueue(downloadQueue,downloadDraft.url);downloadDraft.error='';downloadDraft.url='';$('#download-error').hidden=true;$('#download-url').value='';$('#download-url').removeAttribute('aria-invalid');downloadFilter='all';refreshDownload();ensureDownloadTimer();toast('Đã thêm '+count+' lượt tải mẫu.');
  }catch(error){downloadDraft.error=error.message;$('#download-error').textContent=error.message;$('#download-error').hidden=false;$('#download-url').setAttribute('aria-invalid','true');$('#download-url').focus();}
}
/* "Mở trên điện thoại": status, link, code and the switch, on the PC only (plan §12.3). */
function phonePanel(){
  const warn='<p class="muted">Chỉ dùng trong Wi-Fi nhà: kết nối là HTTP, không mã hóa; không dùng Wi-Fi công cộng. Khi chế độ này bật, người có mã (hoặc lấy được phiên trong cùng Wi-Fi) có thể xóa vĩnh viễn video gốc đủ điều kiện; tắt khi không dùng. Lần đầu Windows hỏi cho Python qua tường lửa, chọn <strong>Private networks</strong>.</p>';
  if(!LIVE)return '<section class="panel phone-panel" style="margin-bottom:18px"><h2>Mở trên điện thoại</h2><p class="muted">Chỉ có ở bản live (/dashboard-v2/) trên PC.</p></section>';
  const p=state.phone;
  if(state.remote)return '<section class="panel phone-panel" style="margin-bottom:18px"><h2>Đang mở qua điện thoại / laptop</h2><p class="pc-only-note">Xóa video gốc, Xóa video và Dọn video mất gốc làm được ở đây: video gốc bị xóa vĩnh viễn, không qua Thùng rác. Các thao tác sau chỉ làm trên PC: lưu trữ, khôi phục bản xuất, kiểm tra lại Thùng rác; tắt Control Center; cấu hình và đăng nhập AI; Visual AI Audit (gửi ảnh ra ngoài máy); sửa hoặc xóa bộ nhớ logo; bật/tắt chế độ điện thoại. Duyệt cảnh ở đây vẫn có thể thêm hoặc bỏ logo đã nhớ.</p>'+warn+'</section>';
  if(!p)return '<section class="panel phone-panel" style="margin-bottom:18px"><h2>Mở trên điện thoại</h2><p class="muted">Đang tải trạng thái…</p></section>';
  if(p.unavailable)return '<section class="panel phone-panel" style="margin-bottom:18px"><h2>Mở trên điện thoại</h2><p class="notice">Control Center đang chạy chưa có chế độ điện thoại. Tắt bằng Stop-BiliFlow.cmd rồi mở lại bằng Start-BiliFlow-Phone.cmd.</p></section>';
  const body=p.enabled?
    '<div class="key-value"><span>Trạng thái</span><span><strong>Đang bật</strong>'+(p.locked?' · đã khóa nhập mã (sai '+p.failed_attempts+' lần)':p.failed_attempts?' · '+p.failed_attempts+'/'+p.max_failed_attempts+' lần nhập sai':'')+'</span></div>'+
    '<div class="key-value"><span>Mở trên điện thoại</span><span class="mono phone-link">'+esc(p.url)+'</span></div>'+
    '<div class="key-value"><span>Mã truy cập</span><span class="mono phone-code">'+esc(p.code)+'</span></div><p class="muted">Mở link trên điện thoại rồi gõ mã vào ô “Mã truy cập”.</p>'+
    (p.expires_at?'<div class="key-value"><span>Tự tắt lúc</span><span>'+esc(new Date(p.expires_at*1000).toLocaleString('vi-VN'))+' (sau 8 giờ, hoặc khi địa chỉ Wi-Fi đổi)</span></div>':'')+
    (p.locked?'<p class="notice">Đã nhập sai mã quá nhiều lần nên nhập mã đang bị khóa. '+(p.unlock_locked?'Khóa mở đặc biệt cũng đã bị khóa; tắt rồi bật lại để có mã mới.':'Trên điện thoại có thể gỡ bằng khóa mở đặc biệt (còn '+Math.max(0,(p.max_unlocks||0)-(p.unlocks||0))+' lượt), sau đó vẫn phải nhập mã; hoặc tắt rồi bật lại để có mã mới.')+'</p>':'')+
    '<div class="action-grid"><button class="secondary" data-action="phone-extend">Gia hạn thêm 8 giờ</button><button class="danger" data-action="phone-toggle" data-enabled="0">Tắt chế độ điện thoại</button></div>':
    '<div class="key-value"><span>Trạng thái</span><span>Đang tắt'+(p.last_disabled_reason_text?' · lần trước tắt vì '+esc(p.last_disabled_reason_text):'')+'</span></div><p class="muted">Bật để điện thoại hoặc laptop cùng Wi-Fi nhà mở được BiliFlow bằng link và mã. Mã đổi mỗi lần bật.</p>'+
    '<div class="action-grid"><button class="primary" data-action="phone-toggle" data-enabled="1">Bật chế độ điện thoại</button></div>';
  const events=Array.isArray(p.events)&&p.events.length?'<details class="detail-section phone-events" data-fold="phone-events"><summary>Nhật ký gần đây ('+p.events.length+')</summary><ul class="confirm-list">'+p.events.map(e=>'<li><strong>'+esc(e.message||e.type)+'</strong><small>'+esc(new Date((e.at||0)*1000).toLocaleString('vi-VN'))+(e.ip?' · '+esc(e.ip):'')+'</small></li>').join('')+'</ul></details>':'';
  return '<section class="panel phone-panel" style="margin-bottom:18px" aria-label="Mở trên điện thoại"><h2>Mở trên điện thoại</h2>'+body+events+warn+'</section>';
}
function settingsView(){
  const ai=state.ai;
  return heading('Cài đặt không gian','Thiết lập AI Supervisor và xem trạng thái hệ thống.','<span class="badge">'+(ai.ready?'AI đã kết nối':'AI chưa sẵn sàng')+'</span>')+phonePanel()+'<div class="settings-grid"><section class="panel"><h2>AI Supervisor</h2><p class="muted" style="font-size:11px">Kiểm tra tùy chọn bằng tài khoản ChatGPT. Mọi gợi ý vẫn cần bạn duyệt.</p><label class="check-line"><input id="ai-enabled" type="checkbox" '+(ai.config.enabled?'checked':'')+'><span>Bật AI Supervisor<small>JSON audit và Visual AI Audit dùng chung phiên.</small></span></label><label class="field"><span>Model</span><select id="ai-model">'+(Array.isArray(ai.models)&&ai.models.length?ai.models:['gpt-5.6-luna','gpt-5.6-terra','gpt-5.6-sol']).map(m=>'<option '+(ai.config.model===m?'selected':'')+'>'+m+'</option>').join('')+'</select></label><label class="field"><span>Mức suy luận</span><select id="ai-effort">'+(Array.isArray(ai.efforts)&&ai.efforts.length?ai.efforts:['low','medium','high']).map(m=>'<option '+(ai.config.reasoning_effort===m?'selected':'')+'>'+m+'</option>').join('')+'</select></label><div class="action-grid"><button class="primary" data-action="ai-save" '+(state.offline?'disabled':'')+pcOnly('cấu hình AI Supervisor')+'>Lưu cấu hình</button><button class="secondary" data-action="ai-check" '+(state.offline?'disabled':'')+'>Kiểm tra kết nối</button><button class="secondary" data-action="ai-login" '+(state.offline?'disabled':'')+pcOnly('đăng nhập ChatGPT')+'>Đăng nhập ChatGPT</button></div><small>'+esc(ai.message)+(LIVE?'':' · dữ liệu mẫu')+'</small></section><section class="panel"><h2>Hệ thống cục bộ</h2><div class="key-value"><span>Phiên bản</span><span>'+state.version+' · hợp đồng hiện tại</span></div><div class="key-value"><span>Dữ liệu</span><span>E:\\DungChung\\BiliFlow</span></div><div class="key-value"><span>Worker</span><span>1 GPU · hàng đợi FIFO</span></div><div class="key-value"><span>Xuất mặc định</span><span>Tối đa 3,5 GB</span></div><p class="muted" style="font-size:11px">Đóng tab trình duyệt không dừng Control Center.</p><div class="action-grid"><button class="secondary" data-action="shutdown" data-mode="after_stage" '+(state.offline?'disabled':'')+pcOnly('tắt Control Center')+'>Tắt sau bước hiện tại</button><button class="danger" data-action="shutdown" data-mode="immediate" '+(state.offline?'disabled':'')+pcOnly('tắt Control Center')+'>Tắt ngay</button></div>'+(LIVE?'':'<div class="detail-section"><h3>Tình huống kiểm thử demo</h3><label class="field"><span>Trạng thái mô phỏng</span><select id="scenario">'+scenarioOptions()+'</select><small>Chỉ thay đổi dữ liệu mẫu của bản demo này.</small></label><button class="secondary small" data-action="reset">Đặt lại dữ liệu mẫu</button></div>')+'</section></div>'+(LIVE?'':'<section class="panel" style="margin-top:18px"><h2>Lịch sử thao tác mẫu</h2><ol class="log-list">'+state.requests.slice(-8).reverse().map(r=>'<li>'+esc(r.method+' '+r.path)+'<small> · '+esc(r.operation)+'</small></li>').join('')+'</ol>'+(state.requests.length?'':'<p class="muted" style="font-size:11px">Chưa có thao tác. Bạn có thể thử duyệt, xuất hoặc lưu trữ một video mẫu.</p>')+'</section>');
}
/* U1: open <details data-fold> keep their state across re-renders (keyed, never by position). */
function foldState(root){const out={};if(root)root.querySelectorAll('details[data-fold]').forEach(d=>{out[d.dataset.fold]=d.open;});return out;}
function restoreFolds(root,saved){if(root)root.querySelectorAll('details[data-fold]').forEach(d=>{if(d.dataset.fold in saved)d.open=saved[d.dataset.fold];});}
let lastMainHtml=null,shownNotice=null; // shownNotice: the banner on the page ("Tải video" refreshes in place)
function topNotice(){
  return state.offline?'<div class="notice" role="alert">Mất kết nối hệ thống · đang hiển thị dữ liệu đã tải. Thao tác thay đổi được khóa đến khi kết nối lại.</div>':state.source_cleanup_running?'<div class="notice">Một thao tác với video gốc đang chạy. Đợi hoàn tất trước khi xóa, lưu trữ hoặc khôi phục.</div>':'';
}
function mainHtml(){
  return topNotice()+(view==='downloads'?downloadsView():view==='queue'?queueView():view==='logos'?logosView():view==='settings'?settingsView():heading(view==='overview'?'Trung tâm xử lý':'Video của bạn',view==='overview'?'Theo dõi tiến trình, duyệt cảnh và hoàn tất video của bạn.':'Tìm nhanh video và tiếp tục công việc ở đúng bước.')+(view==='overview'?kpis()+hero():'')+list());
}
function render(){
  nav();
  const main=$('#main'),saved=foldState(main),html=mainHtml();
  main.innerHTML=html;lastMainHtml=html;shownNotice=topNotice();restoreFolds(main,saved);
}
function refreshList(){const body=$('#list-body');if(!body)return;const saved=foldState(body);body.innerHTML=listBody();restoreFolds(body,saved);lastMainHtml=null;}
function changeFilter(f,fromSummary){filter=f;page=1;if(fromSummary)query='';if(!['overview','videos'].includes(view)){view='videos';location.hash='videos';}render();$('#video-list')?.scrollIntoView({block:'start',behavior:'instant'});}
/* U2: the scrolling element is aside.drawer (.drawer{overflow:auto}), not .drawer-body. A snapshot whose drawer
   markup is unchanged leaves the drawer untouched (nodes, scroll, open sections, focus, selection, poster); a changed
   one is rebuilt and gets its scroll, open sections and focus back. */
let lastDrawerHtml=null;
function refreshDrawer(){
  const root=$('#drawer-root'),aside=root.querySelector('.drawer');if(!currentJob||!aside)return;
  const j=getJob(currentJob);if(!j){closeDrawer();return;}
  const html=drawerHtml(j);if(html===lastDrawerHtml)return;
  const body=root.querySelector('.drawer-body'),open=[...root.querySelectorAll('details')].map(d=>d.open),scroll=aside.scrollTop,bodyScroll=body?body.scrollTop:0,focus=document.activeElement;
  const key=focus&&root.contains(focus)?[focus.dataset.action,focus.dataset.op,focus.tagName,[...root.querySelectorAll(focus.tagName)].indexOf(focus)]:null;
  openDrawer(currentJob,true);
  root.querySelectorAll('details').forEach((d,i)=>{if(i<open.length)d.open=open[i];});
  const next=root.querySelector('.drawer'),nextBody=root.querySelector('.drawer-body');
  if(nextBody)nextBody.scrollTop=bodyScroll;
  if(next)next.scrollTop=scroll;
  if(key){const same=[...root.querySelectorAll(key[2])],target=key[0]?same.find(el=>el.dataset.action===key[0]&&el.dataset.op===key[1]&&!el.disabled):same[key[3]];if(target)target.focus({preventScroll:true});}
}
function openDrawer(id,refresh){
  const j=getJob(id);if(!j){if(refresh)closeDrawer();return;}currentJob=j.id;if(!refresh)drawerFocus=document.activeElement;
  const html=drawerHtml(j);
  $('#drawer-root').innerHTML=html;lastDrawerHtml=html;
  document.body.style.overflow='hidden';if(!refresh)$('.drawer [data-action="close-drawer"]').focus();
}
function drawerHtml(j){
  const review=j.review_summary,structure=j.structure_audit,ai=j.ai_audit,sourceInfo=C.sourceLine(j);
  const exporting=C.tab(j)==='export',progress=exporting?renderPercent(j):percent(j.progress);
  const running=['scanning','export'].includes(C.tab(j))&&j.state!=='QUEUED';
  const progressText=exporting?stageLabel(j.render_progress?.state||j.state):stageLabel(j.current_stage||j.state);
  const progressNote=j.state==='QUEUED'?'Video đang chờ đến lượt xử lý.':j.state==='WAITING_REVIEW'?reviewRemaining(j)+' cảnh cần quyết định của bạn trước khi xuất.':j.state==='READY_TO_EXPORT'?(j.source_present===false?C.SOURCE_MISSING_MESSAGE:'Các cảnh đã được duyệt. Bạn có thể xuất video.'):running?'Bạn có thể tiếp tục sử dụng dashboard trong lúc video được xử lý.':C.phase(j);
  const checkTiles=[
    ['Kiểm tra cấu trúc',structure?.result||'Chưa kiểm tra',structure?.outdated_rule?'Kết quả theo quy tắc cũ':structure?.summary||'Có sau khi tạo danh sách cảnh duyệt'],
    ['Kiểm tra bằng AI',ai?.state||'Chưa chạy',ai?.summary||'Kiểm tra tùy chọn'],
    j.state==='READY_TO_EXPORT'&&j.source_present===false?['Xuất video','Thiếu video gốc',C.SOURCE_MISSING_MESSAGE]:['Xuất video',exporting?(j.render_progress?.state==='VERIFYING'?'Đang kiểm tra':C.labels[j.state]):j.state==='COMPLETED'?'Đã hoàn tất':'Chưa xuất',j.queue_kind==='export'&&j.queue_position?'Lượt #'+j.queue_position:review?reviewRemaining(j)+' cảnh chưa quyết định cuối':'Cần quét xong']
  ];
  return(
    '<button class="drawer-backdrop" data-action="close-drawer" aria-label="Đóng chi tiết video" tabindex="-1"></button>'+
    '<aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">'+
      '<div class="drawer-head"><div><small>VIDEO #'+j.id+' · PHIÊN '+j.active_revision+'</small><h2 id="drawer-title">'+esc(j.name)+'</h2></div><button class="icon-button" data-action="close-drawer" aria-label="Đóng chi tiết">×</button></div>'+
      '<div class="drawer-body">'+
        '<img class="drawer-poster" src="assets/poster-'+j.palette+'.svg" alt="'+(LIVE?'Hình minh họa':'Hình minh họa video mẫu')+'">'+
        '<div class="drawer-heading">'+badge(j)+'<span class="muted">'+j.duration+' · '+bytes(j.source_size_bytes)+'</span></div>'+
        (j.error?'<p class="notice" role="alert">'+esc(j.error)+'</p>':'')+
        '<section class="progress-summary" aria-label="Tiến trình video">'+
          '<div class="eyebrow">'+(running?'BƯỚC ĐANG CHẠY':'TRẠNG THÁI HIỆN TẠI')+'</div>'+
          '<div class="hero-status"><span>'+esc(running?progressText:C.phase(j))+'</span>'+(running?'<strong>'+progress+'%</strong>':'')+'</div>'+
          (running?'<div class="meter" role="progressbar" aria-label="Tiến độ video" aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+progress+'"><i style="width:'+progress+'%"></i></div>':'')+
          '<p>'+esc(progressNote)+'</p>'+
          (sourceInfo?'<p class="source-line tone-'+sourceInfo[1]+'"'+(sourceInfo[1]==='error'?' role="status"':'')+'>'+esc(sourceInfo[0])+'</p>':'')+
        '</section>'+
        drawerActions(j)+
        '<section class="detail-section"><h3>Phạm vi kiểm tra</h3>'+scope(j)+'<p class="muted">'+(j.detector_groups.length<4?'Chỉ các nhóm đã chọn được kiểm tra. Video này chưa chọn đủ bốn nhóm.':'Đã chọn đầy đủ bốn nhóm kiểm tra.')+'</p>'+
          '<div class="key-value"><span>Loại nội dung</span><span>'+esc(({animation:'Hoạt hình',live_action:'Phim người thật',mixed:'Nội dung hỗn hợp'})[j.content_style]||'Chưa thiết lập')+'</span></div>'+
          '<div class="key-value"><span>Chế độ quét</span><span>'+esc(({careful:'Tỉ mỉ',fast:'Nhanh'})[j.profile]||'Chưa thiết lập')+' · OCR '+(j.ocr_recognition_batch_size===8?'tăng tốc thử nghiệm':'chuẩn')+'</span></div>'+
          '<div class="key-value"><span>Tăng tốc</span><span>'+(j.fast_scan?'Bật · giữ mật độ quét':'Tắt')+'</span></div>'+
        '</section>'+
        '<details class="detail-section audit-details"><summary>Kết quả kiểm tra & xuất video</summary><div class="detail-grid">'+checkTiles.map(([title,value,note])=>'<div class="detail-tile"><small>'+title+'</small><strong>'+esc(value)+'</strong><p>'+esc(note)+'</p></div>').join('')+'</div></details>'+
        '<details class="detail-section technical"><summary>Video gốc & thông tin kỹ thuật</summary>'+
          '<p class="muted">'+(C.archived(j)?'Đang lưu trữ. Khôi phục sẽ trả video gốc về input để xuất lại.':C.cleaned(j)?'Đã vào Thùng rác. Khôi phục đúng tên, đường dẫn và SHA-256 trước khi xử lý lại.':j.source_present===false?'Không còn video gốc trong input'+(j.delete?.eligible&&!j.protected?' · bấm “Xóa video” để xóa video này khỏi BiliFlow (output giữ nguyên).':''):'Có trong input · report và quyết định duyệt được giữ.')+'</p>'+(j.protected?'<p class="muted">'+esc(j.protected)+'</p>':'')+
          '<div class="key-value"><span>Job key</span><span class="mono">'+esc(j.job_key)+'</span></div>'+
          '<div class="key-value"><span>Nguồn</span><span class="mono">'+esc(j.source_path)+'</span></div>'+
          '<div class="key-value"><span>SHA-256 mẫu</span><span class="mono">'+esc(j.source_sha256)+'</span></div>'+
          '<div class="key-value"><span>Bản xuất</span><span class="mono">'+esc(j.output_path||'Chưa có')+'</span></div>'+
          '<div class="key-value"><span>Hàng đợi</span><span>'+esc(j.queue_position?'Lượt #'+j.queue_position+' · '+(j.queue_kind==='export'?'Xuất video':'Quét cảnh'):'Không chờ worker')+'</span></div>'+
        '</details>'+
      '</div>'+
    '</aside>');
}
function closeDrawer(){ $('#drawer-root').innerHTML='';lastDrawerHtml=null;currentJob=null;document.body.style.overflow='';if(drawerFocus?.isConnected)drawerFocus.focus();}
function showModal(title,body,commit,label){
  scanFormJob=null;exportFormJob=null;
  modalFocus=document.activeElement;modalCommit=commit;modalBusy=false;
  $('#modal-content').innerHTML='<div class="modal-head"><div><h2 id="modal-title">'+esc(title)+'</h2><small>'+(LIVE?'Gửi đến Control Center trên máy này':'Bản demo · chỉ thay đổi dữ liệu mẫu')+'</small></div><button class="icon-button" data-action="close-modal" aria-label="Đóng hộp thoại">×</button></div><div class="modal-body">'+body+'<div class="modal-error" id="modal-error" role="alert" hidden></div></div><div class="modal-foot"><button class="secondary" data-action="close-modal" autofocus>'+(commit?'Hủy':'Đóng')+'</button>'+(commit?'<button class="primary" id="confirm-action" data-action="confirm">'+esc(label||'Xác nhận')+'</button>':'')+'</div>';
  if(!$('#modal').open)$('#modal').showModal();
}
function closeModal(){if(modalBusy)return;$('#modal').close();modalCommit=null;if(modalFocus?.isConnected)modalFocus.focus();}
function toast(message,error){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').className=error?'error':'';$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,5500);}
async function mutate(operation,j,body){
  if(state.offline)throw new Error('Mất kết nối. Chưa gửi thao tác.');
  // R4-U2: finalize of a COMPLETED video is "Xuất lại" (also from the review dialog when nothing was changed).
  const gate=operation==='finalize'&&j&&j.state==='COMPLETED'?'reexport':operation;
  const gated=j&&ops(j).find(a=>a.id===gate);
  if(j&&gated&&!gated.enabled)throw new Error(gated.reason);
  if(j&&!gated&&!['decision','clear','bulkKeep','bulkAccept'].includes(operation))throw new Error('Trạng thái video không còn cho phép thao tác này.');
  if(operation==='start'||operation==='rerun')C.validateScan(body,operation==='start');
  if(operation==='finalize')C.exportSelection(body.size_mode,body.max_output_gb);
  const result=await store.dispatch(operation,j,body);
  return result;
}
function simpleConfirm(j,a){
  const text={cancel:'Hủy xử lý video này? Video gốc, report và quyết định duyệt vẫn được giữ. Dừng chỉ tạm dừng; Bỏ qua đánh dấu xong mà không xuất.',
    skip:'Đánh dấu video hoàn tất mà không xuất? Video gốc và report vẫn được giữ.',
    unskip:'Mở lại video để xuất? Video sẽ quay về mục Chờ duyệt.',
    pause:'Dừng ngay bước hiện tại? Bạn có thể tiếp tục sau.',
    stopAfter:'Dừng sau khi bước hiện tại hoàn tất?',
    hide:'Ẩn video đã hủy khỏi danh sách chính? Có thể hiện lại trong nhóm Đã ẩn.',
    unhide:'Hiện lại video trong nhóm Đã hủy?',
    restore:'Trả video gốc từ archive về đúng đường dẫn input sau khi kiểm SHA-256? Với video đã xuất, bạn cần xuất lại; video đã bỏ qua vẫn giữ trạng thái bỏ qua.',
    retry:'Thử lại bước lỗi của video này?',resume:'Tiếp tục xử lý video, giữ vị trí hàng đợi đã có?',
    recheck:'Đọc lại Thùng rác và ghi kết quả kiểm tra? Các bản ghi dọn/lưu trữ cũ được giữ nguyên.'};
  showModal(a.label,'<p>'+esc(text[a.id]||a.label+'?')+'</p><p><strong>#'+j.id+' · '+esc(j.name)+'</strong></p>',()=>{
    let body={};if(a.id==='recheck')body=j.source_cleanup?.state==='RECYCLED'?{kind:'source_cleanup',id:j.source_cleanup.id}:{kind:'archive_export',id:j.source_archive.id};
    return mutate(a.id,j,body).then(()=>{toast(sent(a.label+' · #'+j.id));return true;});
  },a.label);
}
function scanForm(j,rerun){
  const draft=scanDrafts.get(draftKey(j))||{detectors:j.detector_groups.length?[...j.detector_groups]:Object.keys(C.detectors),ocr_recognition_batch_size:j.ocr_recognition_batch_size,fast_scan:j.fast_scan,content_style:j.content_style,profile:j.profile};
  showModal(rerun?'Chạy lại kiểm tra':'Thiết lập video','#'+j.id+' · '+esc(j.name)+'<div class="scope-options">'+Object.entries(C.detectors).map(([id,label])=>'<label class="scope-option"><input type="checkbox" name="detector" value="'+id+'" '+(draft.detectors.includes(id)?'checked':'')+'>'+label+'</label>').join('')+'</div>'+(rerun?'':'<div class="form-columns"><label class="field"><span>Loại nội dung</span><select id="scan-style">'+[['animation','Hoạt hình'],['live_action','Phim thực tế'],['mixed','Hỗn hợp']].map(([id,label])=>'<option value="'+id+'" '+(draft.content_style===id?'selected':'')+'>'+label+'</option>').join('')+'</select></label><label class="field"><span>Chế độ quét</span><select id="scan-profile"><option value="careful" '+(draft.profile==='careful'?'selected':'')+'>Tỉ mỉ</option><option value="fast" '+(draft.profile==='fast'?'selected':'')+'>Nhanh · giảm mật độ quét</option></select></label></div>')+'<label class="field"><span>OCR quảng cáo / logo</span><select id="scan-ocr"><option value="1" '+(draft.ocr_recognition_batch_size===1?'selected':'')+'>Chuẩn (mặc định)</option><option value="8" '+(draft.ocr_recognition_batch_size===8?'selected':'')+'>Tăng tốc · thử nghiệm</option></select></label><label class="check-line"><input id="scan-fast" type="checkbox" '+(draft.fast_scan?'checked':'')+'><span>Tăng tốc xử lý<small>Giữ mật độ quét. Đây là tùy chọn riêng với chế độ Nhanh ở trên.</small></span></label><p>'+(rerun?'Tạo revision mới; report và quyết định cũ được giữ.':'Xác nhận phạm vi đã chọn trước khi xếp video vào hàng đợi.')+'</p>',()=>{
    const data={detectors:[...document.querySelectorAll('input[name="detector"]:checked')].map(x=>x.value),ocr_recognition_batch_size:Number($('#scan-ocr').value),fast_scan:$('#scan-fast').checked};
    if(!rerun){data.content_style=$('#scan-style').value;data.profile=$('#scan-profile').value;}
    C.validateScan(data,!rerun);scanDrafts.set(draftKey(j),{...draft,...data});
    showModal('Xác nhận phạm vi kiểm tra','<p><strong>#'+j.id+' · '+esc(j.name)+'</strong></p><p>Nhóm đã chọn: '+data.detectors.map(id=>esc(C.detectors[id])).join(', ')+'</p><p>Nhóm chưa kiểm tra: '+(Object.keys(C.detectors).filter(id=>!data.detectors.includes(id)).map(id=>esc(C.detectors[id])).join(', ')||'Không có')+'</p><p>'+ (rerun?'Lượt mới sẽ có revision riêng.':'Video sẽ được xếp theo thứ tự bạn bấm.')+'</p>',async()=>{await mutate(rerun?'rerun':'start',j,data);scanDrafts.delete(draftKey(j));toast((LIVE?'Đã gửi lệnh quét #':'Đã xếp video mẫu #')+j.id+(LIVE?'; xem thứ tự trong Hàng đợi.':' vào hàng đợi quét.'));return true;},rerun?'Xác nhận chạy lại':'Xác nhận bắt đầu');
    return false;
  },'Kiểm tra lựa chọn');
  scanFormJob=j.id;
}
let exportFormJob=null;
/* "Xuất video đã duyệt": the size choices, limits and confirm sentence of export_dialog.py; finalize is sent once,
   only from "Xác nhận xuất video", never retried. options (review dialog, R3): {resources, onQueued(result)};
   options.reexport (R4-U2 "Xuất lại"): {intro, guide} replace the decisions line and follow the queue note. */
function exportModal(j,options){
  options=options||{};
  const resources=C.resourceItems(options.resources),again=options.reexport;
  showModal(again?'Xuất lại video':'Xuất video đã duyệt','<p><strong>#'+j.id+' · '+esc(j.name)+'</strong></p>'+(again?again.intro:'<p>'+ (C.reviewStats(j).total)+' cảnh đã có quyết định cuối cùng. Xuất khóa các lựa chọn hiện tại.</p>')+(resources.length?'<div class="export-resources">'+resources.map(([label,value])=>'<span>'+esc(label)+' <strong>'+esc(value)+'</strong></span>').join('')+'</div>':'')+'<label class="field"><span>Giới hạn dung lượng bản xuất</span><select id="export-mode">'+C.EXPORT_SIZE_OPTIONS.map(([value,label])=>'<option value="'+value+'">'+esc(label)+'</option>').join('')+'</select></label><label class="field" id="custom-size" hidden><span>Dung lượng tối đa (GB)</span><input id="export-gb" '+C.EXPORT_CUSTOM_GB.attributes+' value="'+C.EXPORT_CUSTOM_GB.value+'"></label><p id="export-confirm-text" class="export-confirm"></p><p>Video được xếp vào hàng đợi xuất. Trạng thái hoàn tất chỉ xuất hiện sau khi xuất và kiểm tra xong.</p>'+(again?again.guide:''),async()=>{
    const selection=C.exportSelection($('#export-mode').value,$('#export-gb').value);
    const result=await mutate('finalize',j,selection);exportDrafts.delete(draftKey(j));
    const status=result&&result.body&&result.body.status;
    toast(LIVE?(status==='COMPLETED'?'Bản xuất của lần duyệt này đã có (manifest khớp); không xuất lại.':'Đã xếp lệnh xuất #'+j.id+'. Hoàn tất chỉ hiện sau khi xuất và kiểm tra xong.'):'Đã xếp video mẫu #'+j.id+' vào hàng đợi xuất.');
    if(options.onQueued)options.onQueued(result);
    return true;
  },again?'Xuất lại':'Xác nhận xuất video');
  exportFormJob=j;
  const choice=C.exportPolicyChoice(j.review_summary?.export_size_policy),saved=exportDrafts.get(draftKey(j));
  const mode=saved?saved.mode:choice.mode;
  $('#export-mode').value=mode;$('#export-gb').value=saved?saved.gb:choice.gb;$('#custom-size').hidden=mode!=='custom';
  exportConfirmLine();
}
/* R4-U2 "Xuất lại" (Hoàn tất): finalize with the decisions of the last review. While the export of that review is in
   output (C.reexportState, from the cleanup hint) the backend only reuses it: the dialog then has no confirm and says
   how to export again (move the export and its manifest out of output). Every case says how to choose again. */
function reexportGuide(fromReview){
  const decide='Đổi quyết định của những cảnh cần sửa'+(fromReview?' ngay trong hộp duyệt cảnh này':'')+'. Video chuyển về <strong>Sẵn sàng xuất</strong> (hoặc <strong>Chờ duyệt</strong> nếu còn cảnh chưa quyết định).';
  const steps=fromReview?[decide,'Bấm <strong>Xuất video</strong> lần nữa rồi xác nhận xuất.']:['Bấm <strong>⋯</strong> ở video này rồi bấm <strong>Duyệt cảnh</strong>.',decide,'Bấm <strong>Xuất video</strong> trong hộp duyệt cảnh rồi xác nhận xuất.'];
  return '<h3>Muốn duyệt lại và xuất với lựa chọn mới</h3><ol class="help-steps">'+steps.map(s=>'<li>'+s+'</li>').join('')+'</ol><p class="muted">Bản xuất cũ vẫn giữ nguyên trong thư mục output; BiliFlow không ghi đè.</p>';
}
function reexportModal(j,options){
  options=options||{};
  const x=C.reexportState(j),guide=reexportGuide(options.fromReview);
  const warn='<p class="notice" role="note"><strong>Xuất lại dùng các lựa chọn cũ:</strong> đúng các quyết định đã duyệt ở lần xuất trước. Không cảnh nào được duyệt lại.</p>';
  if(x.present){
    const stamp=C.formatStamp(x.exportedAt),facts=[C.formatBytes(x.bytes),stamp?'xuất lúc '+stamp:''].filter(Boolean).join(', ');
    showModal('Xuất lại video','<p><strong>#'+j.id+' · '+esc(j.name)+'</strong></p>'+warn+'<p>Bản xuất hiện có: <span class="mono">output\\'+esc(x.name)+'</span>'+(facts?' ('+esc(facts)+')':'')+'. Khi file này còn trong thư mục output, BiliFlow dùng lại nó (manifest khớp lần duyệt này) và không xuất lại.</p><h3>Muốn xuất lại với lựa chọn cũ</h3><p>Ví dụ để áp dụng cách che logo mới: trên PC, dời file <span class="mono">'+esc(x.name)+'</span> và file <span class="mono">'+esc(x.manifest)+'</span> ra khỏi thư mục output (ví dụ vào Thùng rác), rồi bấm <strong>Xuất lại</strong> lần nữa.</p>'+guide,null);
    return;
  }
  const status='<p>'+(x.moved?esc(x.reason)+'. Xuất lại tạo bản xuất mới từ các lựa chọn cũ.':(x.reason?'Tình trạng bản xuất: '+esc(x.reason)+'. ':'')+'Nếu bản xuất của lần duyệt này vẫn còn trong output, BiliFlow dùng lại nó và không xuất lại.')+'</p>';
  exportModal(j,{...options,reexport:{intro:warn+status,guide}});
}
/* The confirm sentence of export_dialog.py for the current choice (or the limit error of a bad custom value). */
function exportConfirmLine(){
  const el=$('#export-confirm-text');if(!el)return;
  try{el.textContent=C.exportConfirmText(C.exportSelection($('#export-mode').value,$('#export-gb').value));el.classList.remove('error');}
  catch(e){el.textContent=e.message;el.classList.add('error');}
}
/* "Xóa video gốc" and "Xóa video" delete for good (2026-10-05): the dialog says what goes and what stays, and the
   confirm button stays off until "Tôi hiểu" is ticked. "Lưu trữ" keeps its Recycle Bin line for the export. */
const FILE_TITLES={cleanup:'Xóa video gốc',delete:'Xóa video',archive:'Lưu trữ video'};
const FILE_TEXTS={
  cleanup:'Video gốc trong input bị xóa vĩnh viễn (không qua Thùng rác) sau khi kiểm SHA-256, cùng manifest của bản xuất và dữ liệu của video trong BiliFlow (báo cáo, quyết định duyệt, log, lịch sử). Sau đó không duyệt hay xuất lại video này được nữa. Giữ lại: file .mp4 đã xuất trong output và bộ nhớ logo/studio.',
  delete:'Video bị xóa khỏi BiliFlow cùng báo cáo, quyết định duyệt, log và lịch sử của nó. Video đã hủy còn video gốc trong input thì video gốc bị xóa vĩnh viễn (không qua Thùng rác) sau khi kiểm SHA-256. Không đụng tới thư mục output và bộ nhớ logo/studio.',
  archive:'Video gốc được chuyển vào archive trên cùng ổ và kiểm SHA-256. Bản xuất và manifest của video đã xuất vào Thùng rác; video bỏ qua chỉ lưu trữ nguồn. Khôi phục trả nguồn về input để xuất lại.'};
const FILE_ACKS={
  cleanup:'Tôi hiểu: video gốc bị xóa vĩnh viễn, không lấy lại được từ Thùng rác; video này và quyết định duyệt của nó bị xóa khỏi BiliFlow, không duyệt hay xuất lại được nữa.',
  delete:'Tôi hiểu: video bị xóa khỏi BiliFlow cùng quyết định duyệt; video gốc của video đã hủy bị xóa vĩnh viễn, không lấy lại được từ Thùng rác.'};
/* The confirm button of a permanent delete stays off while "Tôi hiểu" is not ticked, also after a failed attempt. */
function ackMissing(){const box=$('#ack-permanent');return !!box&&!box.checked;}
function fileEntry(kind,x){
  const name='<strong>#'+x.job_id+' · '+esc(x.name||x.file_name)+'</strong>',size=bytes(Number(x.size_bytes)||0),reports=bytes(Number(x.reports_bytes)||0);
  if(kind==='archive')return '<li>'+name+'<small>'+esc(x.source_path||x.file_name||'')+' · '+size+'</small><small>Nơi lưu: '+esc(x.archive_path||'archive/sources/')+'</small></li>';
  if(kind==='delete')return '<li>'+name+'<small>'+(x.kind==='CANCELLED'?'Đã hủy · xóa vĩnh viễn video gốc '+esc(x.file_name||'')+' ('+size+')':'Mất video gốc · chỉ xóa dữ liệu của video trong BiliFlow')+'</small><small>Báo cáo và log: '+reports+'</small></li>';
  return '<li>'+name+'<small>'+esc(x.source_path||x.file_name||'')+' · '+size+'</small><small>Bản xuất: '+(x.output_name?esc(x.output_name)+' (giữ .mp4, xóa manifest)':esc(x.kind==='SKIPPED'?'Đã bỏ qua · không xuất':'—'))+' · Báo cáo và log: '+reports+'</small></li>';
}
function previewMarkup(kind,p,message){
  const eligible=p.eligible||[],ineligible=p.ineligible||[],rb=p.recycle_bin,permanent=C.permanentOps.includes(kind);
  return (message?'<div class="notice" role="alert">'+esc(message)+'</div>':'')+'<p>'+FILE_TEXTS[kind]+'</p><ul class="confirm-list">'+eligible.map(x=>fileEntry(kind,x)).join('')+'</ul>'+
    (permanent&&eligible.length?'<p class="confirm-total">Tổng: '+eligible.length+' video · video gốc '+bytes(Number(p.total_bytes)||0)+' · báo cáo và log '+bytes(Number(p.reports_bytes)||0)+' được giải phóng.</p>':'')+
    (ineligible.length?'<h3>Không thực hiện ('+ineligible.length+')</h3><ul class="confirm-list">'+ineligible.map(x=>'<li><strong>#'+x.job_id+(x.name?' · '+esc(x.name):'')+'</strong><small>'+esc(x.reason)+'</small></li>').join('')+'</ul>':'')+
    (kind==='archive'?'<div class="bin-preview">'+(rb?'Thùng rác '+esc(rb.volume||'')+' · '+bytes(Number(rb.used_bytes)||0)+(rb.max_bytes?' / '+bytes(Number(rb.max_bytes)):' · không rõ giới hạn')+'<br>Sau thao tác: '+bytes(Number(rb.after_bytes)||0)+' · dự phòng 64 MiB':'Không đọc được Thùng rác: thao tác cần Thùng rác sẽ bị từ chối.')+'</div>':'')+
    (p.blocked?'<div class="notice">Thao tác bị khóa: '+esc(p.blocked)+'</div>':!eligible.length?'<div class="notice">Không có video đủ điều kiện.</div>':'')+
    (permanent&&eligible.length&&!p.blocked?'<label class="check-line ack-line"><input type="checkbox" id="ack-permanent"><span>'+esc(FILE_ACKS[kind])+'</span></label>':'');
}
async function filePreview(kind,ids){
  let p;
  try{p=await store.preview(kind,ids.slice(0,50));}catch(e){toast(e.message,true);return;}
  showPreview(kind,ids,p,'');
}
function showPreview(kind,ids,p,message){
  const eligible=p.eligible||[],blocked=!!p.blocked||!eligible.length||state.source_cleanup_running||state.offline;
  const chosen=eligible.map(x=>x.job_id),permanent=C.permanentOps.includes(kind),done=permanent?'DELETED':'ARCHIVED';
  showModal(FILE_TITLES[kind],previewMarkup(kind,p,message),blocked?null:async()=>{
    if(state.offline||state.source_cleanup_running)throw new Error('Hệ thống không sẵn sàng. Không thực hiện thao tác.');
    if(permanent&&!$('#ack-permanent')?.checked)throw new Error('Đánh dấu “Tôi hiểu” trước khi xóa.');
    let result;
    try{result=await store.fileAction(kind,chosen,p.preview_id);}
    catch(e){
      // 409 with a new preview: show the new list; the user confirms again. Nothing is resent.
      if(e.status===409&&e.preview){showPreview(kind,ids,e.preview,e.message);return false;}
      throw e;
    }
    // A PARTIAL video lost its source too: it can no longer be picked for these actions.
    const results=(result&&result.results)||[],gone=r=>r.status===done||permanent&&r.status==='PARTIAL';
    // DELETED with a note (the export manifest stayed in output) is not a clean result: the list below shows it.
    const ok=results.filter(r=>r.status===done&&!String(r.message||'').includes(C.DELETE_NOTE));
    results.filter(gone).forEach(r=>selected.delete(r.job_id));
    if(ok.length===results.length){toast((LIVE?'Đã ':'Đã mô phỏng ')+(permanent?'xóa':'lưu trữ')+' '+ok.length+' video.');return true;}
    showModal('Kết quả từng video','<ul class="confirm-list">'+results.map(r=>'<li><strong>#'+r.job_id+' · '+esc(r.name||'')+' · '+esc(r.status)+'</strong><small>'+esc(r.message||'')+'</small></li>').join('')+'</ul>'+(results.some(r=>!gone(r))?'<p>Mục chưa thành công vẫn được giữ trong lựa chọn.</p>':''),null);
    return false;
  },kind==='cleanup'?'Xóa vĩnh viễn '+chosen.length+' video gốc':kind==='delete'?'Xóa '+chosen.length+' video':'Lưu trữ '+chosen.length+' video');
  const box=$('#ack-permanent'),go=$('#confirm-action');
  if(permanent&&box&&go){go.disabled=true;box.addEventListener('change',()=>{go.disabled=!box.checked;});}
}
function auditModal(j){
  showModal('Kiểm tra bằng AI Supervisor','<p><strong>#'+j.id+' · '+esc(j.name)+'</strong></p><label class="field"><span>Dữ liệu gửi kiểm tra</span><select id="audit-kind"><option value="json">JSON / báo cáo, không gửi ảnh</option><option value="visual"'+(state.remote?' disabled':'')+'>Visual AI Audit · có ảnh thumbnail'+(state.remote?' (chỉ làm trên PC)':'')+'</option></select></label>'+(state.remote?'<p class="pc-only-note">Chỉ làm trên PC: Visual AI Audit gửi ảnh ra ngoài máy.</p>':'')+'<p>Chọn Visual AI Audit nghĩa là bạn đồng ý gửi tối đa 36 thumbnail của video này qua tài khoản ChatGPT. Video và âm thanh gốc không được gửi. AI chỉ đề xuất; bạn duyệt mọi quyết định.</p>',async()=>{await mutate('audit',j,{visual:$('#audit-kind').value==='visual'});toast(sent('AI audit #'+j.id));return true;},'Xác nhận kiểm tra');
}
function logoAction(key,remove){
  const l=state.logos.find(x=>x.key===key);if(!l)return;const sha=state.memory_sha256;
  const target=l.memory_class==='studio_logo'?'platform_logo':'studio_logo';
  showModal(remove?'Xóa khỏi bộ nhớ logo':'Đổi loại logo','<p><strong>'+esc(l.name)+'</strong></p><p>'+(remove?'Bản ghi được xóa khỏi bộ nhớ; bộ nhớ và ảnh khung hình được sao lưu. Các thẻ đã duyệt không thay đổi.':'Chuyển thành '+(target==='platform_logo'?'logo nền tảng với đề xuất BLUR':'logo hãng phim với đề xuất KEEP')+'. Thay đổi có hiệu lực ở lần quét sau.')+'</p>'+(target==='platform_logo'&&!remove?'<label class="field"><span>Nền tảng</span><select id="logo-platform"><option value="iqiyi">iQIYI</option><option value="youku">Youku</option><option value="tencent_video">Tencent / WeTV</option><option value="mango_tv">Mango TV</option><option value="sohu">Sohu</option><option value="pptv">PPTV</option></select></label>':''),async()=>{
    if(state.offline)throw new Error('Mất kết nối. Chưa gửi thao tác.');
    if(!sha)throw new Error('Chưa tải bộ nhớ logo. Tải lại trang rồi làm lại.');
    const body={key,expected_sha256:sha};if(!remove){body.memory_class=target;if(target==='platform_logo')body.platform=$('#logo-platform').value;}
    await store.logoAction(remove,body);toast(LIVE?'Đã cập nhật bộ nhớ logo.':'Đã cập nhật bộ nhớ logo mẫu.');return true;
  },remove?'Xác nhận xóa khỏi bộ nhớ':'Xác nhận đổi loại');
}
function jobAction(id,operation){
  const j=getJob(id),a=j&&ops(j).find(x=>x.id===operation);if(!a||!a.enabled)return;
  // R4.3: "Duyệt cảnh" opens the review dialog over the current view (live and demo); the classic page stays one link away in it.
  if(operation==='review'){reviewPushed=true;location.hash='review/'+j.id+'/'+view;}
  else if(operation==='start'||operation==='rerun')scanForm(j,operation==='rerun');
  else if(operation==='finalize')exportModal(j);
  else if(operation==='reexport')reexportModal(j);
  else if(operation==='audit')auditModal(j);
  else if(operation==='cleanup'||operation==='archive'||operation==='delete')filePreview(operation,[j.id]);
  else simpleConfirm(j,a);
}
function scenario(name){if(LIVE)return;store.scenario(name);}
document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-action]');if(!el||el.disabled)return;
  const action=el.dataset.action;
  if(DL&&DL.click(el,action,event))return;
  if(action==='detail')openDrawer(el.dataset.id);
  else if(action==='download-start')startDownload();
  else if(action==='download-item'){D.action(downloadQueue,el.dataset.id,el.dataset.op);refreshDownload();ensureDownloadTimer();}
  else if(action==='download-filter'){downloadFilter=el.dataset.filter;refreshDownload();}
  else if(action==='download-toggle'){D.togglePause(downloadQueue);if(downloadQueue.paused){clearInterval(downloadTimer);downloadTimer=null;}refreshDownload();ensureDownloadTimer();}
  else if(action==='download-sample'){
    const base='https://video.example/watch?v=video-demo-';
    $('#download-url').value=[1,2,3].map(n=>base+(downloadQueue.serial+n)).join('\n');startDownload();
  }
  else if(action==='theme'){theme=theme==='light'?'dark':'light';try{localStorage.setItem('biliflow-v2-theme',theme);}catch(_){}applyTheme();}
  else if(action==='close-drawer')closeDrawer();
  else if(action==='close-modal')closeModal();
  else if(action==='filter')changeFilter(el.dataset.filter);
  else if(action==='summary-filter')changeFilter(el.dataset.filter,true);
  else if(action==='prev'){page--;refreshList();}
  else if(action==='next'){page++;refreshList();}
  else if(action==='job')jobAction(el.dataset.id,el.dataset.op);
  else if(action==='scheduler')showModal(state.scheduler_paused?'Tiếp tục hàng đợi':'Tạm dừng hàng đợi','<p>Giữ nguyên thứ tự quét và xuất. Bước đang chạy được giữ riêng; bạn có thể dừng nó trong Chi tiết video.</p>',async()=>{await mutate('scheduler',null,{paused:!state.scheduler_paused});toast(LIVE?'Đã gửi lệnh hàng đợi.':'Đã thay đổi hàng đợi mẫu.');return true;});
  else if(action==='select-all'){selected=new Set(allFiltered().filter(selectable).slice(0,50).map(j=>j.id));refreshList();}
  else if(action==='deselect'){selected.clear();refreshList();}
  else if(action==='bulk-cleanup'||(action==='bulk-archive'&&!state.remote))filePreview(action==='bulk-cleanup'?'cleanup':'archive',[...selected]);
  else if(action==='purge-lost'&&!state.source_cleanup_running&&!state.offline)filePreview('delete',C.lostIds(state.jobs).slice(0,50));
  else if(action==='logo-class'||action==='logo-delete')logoAction(el.dataset.key,action==='logo-delete');
  else if(action==='shutdown')showModal('Tắt BiliFlow','<p>'+ (el.dataset.mode==='immediate'?'Dừng bước hiện tại và tắt Control Center?':'Tắt Control Center sau khi bước hiện tại hoàn tất?')+'</p><p>Đóng tab không dừng backend. '+(LIVE?'Control Center nhận lệnh rồi mới tắt; trang sẽ mất kết nối.':'Ở demo, thao tác này mô phỏng mất kết nối.')+'</p>',async()=>{await mutate('shutdown',null,{mode:el.dataset.mode});toast(LIVE?'Control Center nhận lệnh tắt (202). Chưa chứng minh đã tắt; kiểm tra lại sau.':'Đã mô phỏng lệnh tắt; backend thật vẫn hoạt động.');return true;},'Xác nhận tắt');
  else if(action==='ai-save'){const data={enabled:$('#ai-enabled').checked,model:$('#ai-model').value,reasoning_effort:$('#ai-effort').value};if(el.dataset.busy)return;el.dataset.busy='1';try{await mutate('aiConfig',null,data);aiDirty=false;toast(LIVE?'Đã lưu cấu hình AI.':'Đã lưu cấu hình AI mẫu.');render();}catch(e){toast(e.message,true);}finally{delete el.dataset.busy;}}
  else if(action==='phone-toggle'){
    if(!LIVE||state.remote||el.dataset.busy)return;el.dataset.busy='1';el.disabled=true;const on=el.dataset.enabled==='1';
    try{await store.dispatch('phoneMode',null,{enabled:on});state=store.snapshot();toast(on?'Đã bật chế độ điện thoại. Mã mới hiện trong khung.':'Đã tắt chế độ điện thoại; mã cũ hết hiệu lực.');}
    catch(e){toast(e.message,true);}finally{delete el.dataset.busy;render();}
  }
  else if(action==='phone-extend'){
    if(!LIVE||state.remote||el.dataset.busy)return;el.dataset.busy='1';el.disabled=true;
    try{await store.dispatch('phoneMode',null,{extend:true});state=store.snapshot();toast('Đã gia hạn: chế độ điện thoại tự tắt sau 8 giờ kể từ bây giờ. Mã giữ nguyên.');}
    catch(e){toast(e.message,true);}finally{delete el.dataset.busy;render();}
  }
  else if(action==='ai-check'){if(el.dataset.busy)return;el.dataset.busy='1';try{await mutate('aiCheck',null,{});state=store.snapshot();toast(state.ai.message);render();}catch(e){toast(e.message,true);}finally{delete el.dataset.busy;}}
  else if(action==='ai-login')showModal('Đăng nhập ChatGPT',(LIVE?'<p>Mở luồng đăng nhập ChatGPT hiện có của Codex trên máy này. Không dùng API trả phí.</p>':'<p>Trong bản tích hợp, thao tác này mở luồng đăng nhập hiện có. Demo chỉ mô phỏng trạng thái.</p>'),async()=>{await mutate('aiLogin',null,{});toast(LIVE?'Đã mở luồng đăng nhập ChatGPT hiện có.':'Đã mô phỏng đăng nhập.');return true;});
  else if(action==='help')showModal('Làm việc với BiliFlow V2','<ol class="help-steps"><li><strong>Thiết lập video:</strong> chọn nhóm kiểm tra, loại nội dung và chế độ quét.</li><li><strong>Duyệt cảnh:</strong> quyết định Giữ, Làm mờ, Cắt hoặc Cần xem thêm. Cần xem thêm vẫn chặn xuất.</li><li><strong>Xuất hoặc bỏ qua:</strong> xuất khi mọi cảnh đã quyết định cuối; bỏ qua khi không cần chỉnh sửa.</li><li><strong>Quản lý video gốc:</strong> xóa vĩnh viễn video gốc hoặc lưu trữ sau khi kiểm tra bản xuất; xóa video đã hủy hoặc không còn video gốc khỏi BiliFlow.</li></ol>'+(LIVE?'<p>Dữ liệu lấy từ Control Center trên máy này. Trang Tải video vẫn là mô phỏng.</p>':'<p>Mọi dữ liệu là mẫu. Tải lại trang đặt lại trạng thái. Bản demo không kết nối API thật.</p>')+'',null);
  else if(action==='reset'&&!LIVE){clearInterval(downloadTimer);downloadTimer=null;downloadDraft={url:'',error:''};downloadQueue=D.createQueue();downloadFilter='all';scanDrafts.clear();exportDrafts.clear();selected.clear();filter='all';query='';page=1;closeDrawer();if(!LIVE)store.reset();render();toast('Đã đặt lại dữ liệu mẫu.');}
  else if(action==='confirm'&&modalCommit&&!modalBusy){
    const callback=modalCommit;modalBusy=true;el.disabled=true;
    try{const done=await callback();modalBusy=false;if(done!==false)closeModal();}
    catch(e){modalBusy=false;if($('#modal-error')){$('#modal-error').hidden=false;$('#modal-error').textContent=e.message;}el.disabled=ackMissing();}
    finally{modalBusy=false;if(el.isConnected)el.disabled=ackMissing();}
  }
});
document.addEventListener('change',event=>{
  const el=event.target;
  if(DL&&DL.change(el))return;
  if(scanFormJob && (el.name==='detector'||['scan-ocr','scan-fast','scan-style','scan-profile'].includes(el.id))){
    const j=getJob(scanFormJob);if(!j)return;const old=scanDrafts.get(draftKey(j))||{};
    scanDrafts.set(draftKey(j),{...old,detectors:[...document.querySelectorAll('input[name="detector"]:checked')].map(x=>x.value),ocr_recognition_batch_size:Number($('#scan-ocr').value),fast_scan:$('#scan-fast').checked,content_style:$('#scan-style')?.value||j.content_style,profile:$('#scan-profile')?.value||j.profile});
  }
  if(exportFormJob&&['export-mode','export-gb'].includes(el.id)){exportDrafts.set(draftKey(exportFormJob),{mode:$('#export-mode').value,gb:$('#export-gb').value});exportConfirmLine();}
  if(['ai-enabled','ai-model','ai-effort'].includes(el.id))aiDirty=true;
  if(el.id==='sort'){sort=el.value;page=1;refreshList();}
  else if(el.id==='download-parallel'){D.setParallel(downloadQueue,el.value);refreshDownload();ensureDownloadTimer();}
  else if(el.id==='scenario')scenario(el.value);
  else if(el.id==='export-mode')$('#custom-size').hidden=el.value!=='custom';
  else if(el.matches('[data-select]')){const id=Number(el.dataset.select);if(el.checked&&selected.size>=50){el.checked=false;toast('Mỗi lần chọn tối đa 50 video.',true);return;}el.checked?selected.add(id):selected.delete(id);refreshList();}
});
document.addEventListener('input',event=>{if(DL&&DL.input(event.target))return;if(event.target.id==='export-gb')exportConfirmLine();else if(event.target.id==='search'){query=event.target.value;page=1;refreshList();}else if(event.target.id==='download-url'){downloadDraft.url=event.target.value;downloadDraft.error='';$('#download-error').hidden=true;event.target.removeAttribute('aria-invalid');}});
$('#modal').addEventListener('cancel',event=>{if(modalBusy)event.preventDefault();});
document.addEventListener('keydown',event=>{
  if($('#modal').open)return;
  if(DL&&DL.key(event))return;
  if(currentJob&&event.key==='Escape')closeDrawer();
  if(currentJob&&event.key==='Tab'){
    const items=[...document.querySelectorAll('.drawer button:not(:disabled),.drawer summary,.drawer a,.drawer input,.drawer select')].filter(drawerFocusable);
    const first=items[0],last=items[items.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
});
/* #review/<id>/<view> opens the review dialog over <view>; a malformed review hash goes to #overview. */
let reviewPushed=false;
function route(){
  aiDirty=false;const hash=location.hash.slice(1),target=window.BFReview.parseHash(hash);
  if(!target&&hash.startsWith('review')){history.replaceState(null,'','#overview');}
  const next=target?target.view:(['overview','downloads','videos','queue','logos','settings'].includes(hash)?hash:'overview');
  const wasOpen=review.isOpen();
  closeDrawer();
  if(!target&&wasOpen){reviewPushed=false;review.close();}
  // Opening or closing the dialog over the same view keeps the page as it was (scroll, open sections).
  if(!((target||wasOpen)&&next===view&&$('#main').innerHTML)){view=next;render();window.scrollTo(0,0);}
  if(target)review.open(target.id,target.view);
  if(DL)DL.watch(view==='downloads'); // its list is polled only while the page is open
}
/* Closing (×, Đóng, Esc) returns to <view>: back in history when the dialog was opened from V2, else replace the hash. */
function requestReviewClose(back){
  if(reviewPushed){reviewPushed=false;history.back();return;}
  history.replaceState(null,'','#'+back);route();
}
/* R3: "Xuất video" in the review dialog opens the same export dialog over it, with the job's latest status (the
   dashboard poll is paused while the dialog is open) and the resources line; closing it on success is up to the dialog. */
async function reviewExport(id,options){await store.refresh();state=store.snapshot();const j=getJob(id);if(!j)throw new Error('Không tìm thấy video.');if(j.state==='COMPLETED')reexportModal(j,{...options,fromReview:true});else exportModal(j,options);}
const review=window.BFReview.create({dialog:$('#review-dialog'),overlay:$('#modal'),store,getJob,requestClose:requestReviewClose,toast,exportDialog:reviewExport,
  oldUrl:(id,back)=>LIVE?'/review/'+encodeURIComponent(id)+'?from=v2&view='+encodeURIComponent(back):''});
window.addEventListener('hashchange',route);
/* A new snapshot (polling or after an action) re-renders without losing the user's place. */
function liveChrome(){
  if(!LIVE)return;const d=state.resources&&state.resources.disk||{},strong=$('.storage-mini strong'),bar=$('.storage-mini .meter i');
  if(strong)strong.textContent=Number.isFinite(d.free_bytes)?bytes(d.free_bytes)+' trống':'—';
  if(bar)bar.style.width=(Number(d.percent)||0)+'%';
}
function onSnapshot(){
  state=store.snapshot();liveChrome();review.updateJob();
  if(view==='downloads'&&DL){if(topNotice()!==shownNotice||!DL.refresh())render();else{nav();const mode=$('#dl-mode');if(mode)mode.textContent=state.remote?'Qua điện thoại':'Control Center';}return;}
  if(view==='downloads'||view==='settings'&&(aiDirty||$('#main').contains(document.activeElement))){nav();return;}
  const focus=document.activeElement,id=focus&&focus.id,range=id==='search'?[focus.selectionStart,focus.selectionEnd]:null,y=window.scrollY;
  const inMain=focus&&$('#main').contains(focus)?[focus.dataset.action,focus.dataset.id,focus.dataset.op,focus.dataset.filter]:null;
  // U1: an open <select> (e.g. sort) in #main would be closed by a rebuild: keep #main until it loses focus.
  if(focus&&focus.tagName==='SELECT'&&$('#main').contains(focus)){nav();if(currentJob)refreshDrawer();return;}
  // U1: nothing changed on screen: keep the same nodes (open sections, scroll, selection stay as they are).
  const html=mainHtml();
  if(html===lastMainHtml&&$('#main').innerHTML!==''){nav();if(currentJob)refreshDrawer();return;}
  render();
  if(currentJob)refreshDrawer();
  if(id&&$('#main').contains(document.getElementById(id))){const el=document.getElementById(id);el.focus({preventScroll:true});if(range)el.setSelectionRange(range[0],range[1]);}
  else if(inMain){const el=[...$('#main').querySelectorAll('[data-action]')].find(x=>x.dataset.action===inMain[0]&&x.dataset.id===inMain[1]&&x.dataset.op===inMain[2]&&x.dataset.filter===inMain[3]);if(el)el.focus({preventScroll:true});}
  window.scrollTo(0,y);
}
store.subscribe(onSnapshot);
if(LIVE){
  const pill=$('.demo-pill');if(pill)pill.textContent='CONTROL CENTER';
  const reset=$('[data-action="reset"]');if(reset)reset.remove();
  const mini=$('.storage-mini small');if(mini)mini.textContent='Xem dung lượng thật ở Tổng quan';
  const footer=$('.page-footer span:last-child');if(footer)footer.textContent='Dashboard V2 · route xem thử /dashboard-v2';
  const profile=$('.profile small');if(profile)profile.textContent='Dashboard V2 · xem thử';
  window.addEventListener('hashchange',()=>{if(view==='logos')store.loadMemory().catch(e=>toast(e.message,true));if(view==='settings'){store.loadAI();store.loadPhone();}});
  store.start(3000);
}
route();
if(LIVE&&view==='logos')store.loadMemory().catch(e=>toast(e.message,true));
})();
