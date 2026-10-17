'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const C = require('./contracts.js');
const D = require('./download-demo.js');
let checks=0;
const pending=[];
function check(name,fn){const r=fn();const done=()=>{checks++;process.stdout.write('OK '+name+'\n');};if(r&&typeof r.then==='function')pending.push(r.then(done));else done();}
const job=(state,extra={})=>({id:42,state,source_present:true,review_summary:{status:'READY_FOR_EDIT_PLAN',main_items:2,pending:0,decisions:{KEEP:1,BLUR:1}},...extra});
const has=(j,id,ctx={aiReady:true})=>C.operations(j,ctx).find(a=>a.id===id);
check('Every known state maps to one of the original six buckets',()=>{
  const table={DISCOVERED:'waiting',NEEDS_METADATA:'waiting',PREFLIGHT:'scanning',SCANNING_SAFETY:'scanning',SCANNING_TEXT:'scanning',SCANNING_LOGO:'scanning',LOCALIZING_REGIONS:'scanning',BUILDING_REVIEW:'scanning',AI_AUDITING:'scanning',WAITING_REVIEW:'review',READY_TO_EXPORT:'review',RENDERING:'export',VERIFYING:'export',PAUSED:'waiting',FAILED:'waiting',INTERRUPTED_RECOVERABLE:'waiting',CANCELLED:'waiting',COMPLETED:'completed',SKIPPED:'completed'};
  Object.entries(table).forEach(([s,b])=>assert.equal(C.tab(job(s)),b));
});
check('Queue kind is explicit; unknown queue does not become scan',()=>{
  assert.equal(C.tab(job('QUEUED',{queue_kind:'scan'})),'scan_queue');
  assert.equal(C.tab(job('QUEUED',{queue_kind:'export'})),'export');
  assert.equal(C.tab(job('QUEUED')),'waiting');
});
check('Overview separates scanning, rendering and queue waiting',()=>{
  assert.equal(C.overviewMatch(job('SCANNING_LOGO'),'scan_active'),true);
  assert.equal(C.overviewMatch(job('RENDERING'),'export_active'),true);
  assert.equal(C.overviewMatch(job('VERIFYING'),'export_active'),true);
  assert.equal(C.overviewMatch(job('RENDERING'),'scan_active'),false);
  for(const kind of ['scan','export'])for(const group of Object.keys(C.overviewLabels))assert.equal(C.overviewMatch(job('QUEUED',{queue_kind:kind}),group),false);
});
check('Overview pending review and ready export stay separate',()=>{
  assert.equal(C.overviewMatch(job('WAITING_REVIEW'),'review_pending'),true);
  assert.equal(C.overviewMatch(job('WAITING_REVIEW'),'review_ready'),false);
  assert.equal(C.overviewMatch(job('READY_TO_EXPORT'),'review_ready'),true);
  assert.equal(C.overviewMatch(job('READY_TO_EXPORT'),'review_pending'),false);
  assert.equal(C.overviewMatch(job('CANCELLED',{hidden_at:'x'}),'review_pending'),false);
});
check('Download demo accepts a link from any site, with no source to pick',()=>{
  assert.equal(D.domains,undefined);
  assert.deepEqual(D.validate(' https://video.example/watch?v=demo '),{host:'video.example',url:'https://video.example/watch?v=demo'});
  assert.equal(D.validate('https://phim.example/phim/video-demo').host,'phim.example');
});
check('Download demo rejects blank, unsafe, homepage and IP links',()=>{
  for(const link of ['', 'not a url','javascript:alert(1)','file:///E:/input/video.mp4','https://user:secret@video.example/watch?v=demo','https://video.example:8443/watch?v=demo','https://video.example/','https://127.0.0.1/v','https://[::1]/v','https://localhost/v'])assert.throws(()=>D.validate(link),undefined,link);
});
check('Download demo shows a page it cannot read as not supported',()=>{
  const q=D.createQueue();
  assert.equal(D.enqueue(q,'https://'+D.UNSUPPORTED_PREFIX+'example/phim/1\nhttps://video.example/watch?v=1'),2);
  assert.equal(q.items[0].state,'FAILED');assert.equal(q.items[0].code,'UNSUPPORTED');assert.match(q.items[0].error,/chưa được hỗ trợ/);
  assert.equal(q.items[1].state,'DOWNLOADING');
  assert.equal(D.action(q,q.items[0].id,'retry'),false,'a retry does not make the page supported');assert.equal(q.items[0].state,'FAILED');
});
check('Hidden applies only to cancelled state',()=>{
  assert.equal(C.hidden(job('CANCELLED',{hidden_at:'x'})),true);
  assert.equal(C.hidden(job('COMPLETED',{hidden_at:'x'})),false);
});
check('Download batches validate atomically and reject duplicate links',()=>{
  const q=D.createQueue(),link=n=>'https://video.example/watch?v='+n;
  assert.throws(()=>D.enqueue(q,link(1)+'\nhttps://user@video.example/video'));assert.equal(q.items.length,0);assert.equal(q.serial,0);
  assert.throws(()=>D.enqueue(q,link(1)+'\n'+link(1)));assert.equal(q.items.length,0);
  assert.equal(D.enqueue(q,link(1)+'\n'+link(2)+'\n'+link(3)),3);
  assert.deepEqual(q.items.map(t=>t.state),['DOWNLOADING','DOWNLOADING','QUEUED']);
  assert.throws(()=>D.enqueue(q,link(4)+'\n'+link(1)));assert.equal(q.items.length,3);
});
check('Download slots preserve FIFO and verification must finish before completion',()=>{
  const q=D.createQueue();D.setParallel(q,1);D.enqueue(q,[1,2,3].map(n=>'https://video.example/demo'+n).join('\n'));
  for(let i=0;i<8;i++)D.tick(q);assert.equal(q.items[0].state,'VERIFYING');assert.equal(q.items[0].progress,96);assert.equal(q.items[1].state,'QUEUED');
  D.tick(q);assert.equal(q.items[0].state,'COMPLETED');assert.equal(q.items[1].state,'DOWNLOADING');assert.equal(q.items[2].state,'QUEUED');
  D.setParallel(q,3);assert.equal(D.stats(q).active,2);D.setParallel(q,1);assert.equal(D.stats(q).active,2);
  assert.throws(()=>D.setParallel(q,4));assert.equal(q.parallel,1);
});
check('Pause, cancel, failure and retry keep independent task identity',()=>{
  const q=D.createQueue();D.setParallel(q,1);D.enqueue(q,[1,2].map(n=>'https://video.example/demo'+n).join('\n'));
  D.tick(q);const progress=q.items[0].progress;D.togglePause(q);D.tick(q);assert.equal(q.items[0].progress,progress);D.togglePause(q);
  D.action(q,1,'pause');assert.equal(q.items[0].state,'PAUSED');assert.equal(q.items[1].state,'DOWNLOADING');assert.equal(D.action(q,1,'resume'),false);
  D.action(q,2,'fail');assert.equal(q.items[1].state,'FAILED');assert.equal(D.action(q,1,'resume'),true);assert.equal(q.items[0].progress,progress);
  D.action(q,1,'cancel');D.action(q,2,'retry');assert.equal(q.items[1].id,2);assert.equal(q.items[1].state,'DOWNLOADING');assert.equal(q.items[1].progress,0);assert.equal(q.items[1].error,'');
  assert.equal(D.action(q,1,'pause'),false);D.action(q,1,'retry');assert.deepEqual(q.items.map(t=>t.id),[2,1]);assert.equal(q.items[1].state,'QUEUED');
});
check('Missing source blocks export and rerun',()=>{
  const j=job('READY_TO_EXPORT',{source_present:false});
  assert.equal(has(j,'finalize').enabled,false);assert.equal(has(j,'rerun').enabled,false);
});
check('"Dựng lại danh sách duyệt (giữ quyết định)" sits beside "Chạy lại" for a reviewed or exported video, also on the phone',()=>{
  const label='Dựng lại danh sách duyệt (giữ quyết định)',q={active_queue_path:'demo'};
  for(const state of ['WAITING_REVIEW','READY_TO_EXPORT','COMPLETED']){
    const j=job(state,q),ids=C.operations(j,{aiReady:true}).map(a=>a.id);
    assert.equal(ids.indexOf('rebuild'),ids.indexOf('rerun')+1,state);
    assert.deepEqual([has(j,'rebuild').label,has(j,'rebuild').enabled],[label,true],state);
    assert.equal(has(j,'rebuild',{remote:true}).enabled,true,'the phone may send it, as "Chạy lại"');
  }
  assert.deepEqual([has(job('SKIPPED',q),'rebuild').enabled,has(job('SKIPPED',q),'rebuild').reason],[false,'Video đã bỏ qua: bấm “Mở lại để xuất” trước.']);
  assert.equal(has(job('READY_TO_EXPORT',{...q,render_request:true}),'rebuild').enabled,false,'an export in flight');
  assert.deepEqual([has(job('WAITING_REVIEW',{...q,ai_audit:{state:'RUNNING'}}),'rebuild').enabled,has(job('WAITING_REVIEW',{...q,ai_audit:{state:'RUNNING'}}),'rebuild').reason],[false,'Visual AI Audit đang chạy; chờ xong rồi dựng lại.']);
  assert.equal(has(job('COMPLETED',{...q,source_present:false}),'rebuild').enabled,false,'no source');
  assert.equal(has(job('COMPLETED',{...q,source_archived:true}),'rebuild').reason,'Video đang được lưu trữ. Khôi phục trước khi xử lý.');
  for(const state of ['QUEUED','SCANNING_TEXT','RENDERING','PAUSED','FAILED','CANCELLED'])assert.equal(has(job(state,q),'rebuild'),undefined,state);
  assert.equal(has(job('WAITING_REVIEW',{active_queue_path:null}),'rebuild'),undefined,'no list to keep');
  assert.deepEqual(C.request('rebuild',{id:42},{}),{operation:'rebuild',method:'POST',path:'/api/jobs/42/rebuild-review',body:{}});
  assert.equal(C.REBUILD_CONFIRM.length,4);
  assert.ok(C.REBUILD_CONFIRM[0].includes('chỉ phải duyệt thẻ mới hoặc thẻ đã đổi')&&C.REBUILD_CONFIRM[1].includes('output, không bị xóa')&&C.REBUILD_CONFIRM[2].startsWith('Phải xuất lại'));
  assert.equal(C.rebuildSent({rebuild:{decisions:7,from_revision:3}}),'Đã xếp dựng lại danh sách duyệt: giữ 7 quyết định của revision 3. Khi xong, mở Duyệt cảnh để xem thẻ mới hoặc thẻ đổi.');
});
check('Unresolved NEEDS_MORE_CONTEXT blocks export even with stale ready status',()=>{
  const j=job('READY_TO_EXPORT',{review_summary:{status:'READY_FOR_EDIT_PLAN',main_items:2,pending:0,decisions:{KEEP:1,NEEDS_MORE_CONTEXT:1}}});
  assert.equal(C.reviewStats(j).remaining,1);assert.equal(has(j,'finalize').enabled,false);
});
check('Unfinished render request blocks resubmission and rerun',()=>{
  const j=job('READY_TO_EXPORT',{render_request:true});
  assert.equal(has(j,'finalize').enabled,false);assert.equal(has(j,'rerun').enabled,false);
});
check('Ready resolved queue accepts export',()=>assert.equal(has(job('READY_TO_EXPORT'),'finalize').enabled,true));
check('R4-U2: "Xuất lại" is the main action of an exported video; it needs the source and no export in flight',()=>{
  const done=job('COMPLETED',{active_queue_path:'demo'});
  assert.equal(C.primary(done),'reexport');
  assert.deepEqual([has(done,'reexport').label,has(done,'reexport').enabled],['Xuất lại',true]);
  assert.equal(has(done,'reexport',{remote:true}).enabled,true,'the phone may re-export (finalize is a phone POST)');
  assert.equal(has(job('COMPLETED',{render_request:true}),'reexport').enabled,false);
  const cleanedJob=job('COMPLETED',{source_present:false,source_cleaned:true,source_cleanup:{state:'RECYCLED'}});
  assert.equal(C.primary(cleanedJob),'reexport');
  assert.deepEqual([has(cleanedJob,'reexport').enabled,has(cleanedJob,'reexport').reason],[false,'Khôi phục đúng video từ Thùng rác về input trước.']);
  assert.equal(C.primary(job('COMPLETED',{source_present:false,source_archived:true,source_archive:{state:'ARCHIVED'}})),'restore');
  for(const state of ['READY_TO_EXPORT','SKIPPED','WAITING_REVIEW','RENDERING'])assert.equal(has(job(state,{active_queue_path:'demo'}),'reexport'),undefined,state);
  assert.equal(C.primary(job('SKIPPED',{active_queue_path:'demo'})),'review','a skipped video keeps "Duyệt cảnh"');
  assert.equal(C.primary(job('READY_TO_EXPORT',{source_present:false,active_queue_path:'demo'})),'review');
});
check('R4-U2: the cleanup hint says whether the export of the review is still in output',()=>{
  const present=C.reexportState(job('COMPLETED',{cleanup:{eligible:true,kind:'EXPORTED',output_name:'demo-reviewed.mp4',output_bytes:1048576,exported_at:'2026-10-03T09:20:00Z'}}));
  assert.deepEqual([present.present,present.moved,present.name,present.manifest,present.bytes],[true,false,'demo-reviewed.mp4','demo-reviewed.mp4.manifest.json',1048576]);
  const moved=C.reexportState(job('COMPLETED',{cleanup:{eligible:false,kind:'EXPORTED',reason:C.OUTPUT_MOVED_REASON,output_name:null}}));
  assert.deepEqual([moved.present,moved.moved,moved.reason],[false,true,C.OUTPUT_MOVED_REASON]);
  const other=C.reexportState(job('COMPLETED',{cleanup:{eligible:false,kind:'EXPORTED',reason:'Video gốc đã thay đổi so với lúc quét'}}));
  assert.deepEqual([other.present,other.moved,other.reason],[false,false,'Video gốc đã thay đổi so với lúc quét']);
  // Without the backend's kind and file name an eligible flag proves nothing: finalize decides (it reuses a proven export).
  assert.equal(C.reexportState(job('COMPLETED',{cleanup:{eligible:true}})).present,false);
  assert.equal(C.reexportState(job('COMPLETED')).present,false);
});
check('Skip is only offered when backend certifies skip eligibility',()=>{
  assert.equal(has(job('READY_TO_EXPORT'),'skip'),undefined);
  assert.equal(has(job('READY_TO_EXPORT',{review_summary:{status:'READY_FOR_EDIT_PLAN',main_items:0,pending:0,decisions:{},skip_eligible:true}}),'skip').enabled,true);
});
check('Cleanup requires eligibility rather than state alone',()=>{
  assert.equal(C.eligible(job('COMPLETED'),'cleanup'),false);
  assert.equal(C.eligible(job('COMPLETED',{cleanup:{eligible:true}}),'cleanup'),true);
});
check('A copied eligibility flag cannot unlock archived/cleaned sources',()=>{
  for(const extra of [{source_archived:true},{source_cleaned:true},{source_archive:{state:'RESTORING'}},{source_cleanup:{state:'PENDING'}}]){
    assert.equal(C.eligible(job('COMPLETED',{cleanup:{eligible:true},...extra}),'cleanup'),false);
  }
});
check('Global file operation lock blocks cleanup and restore',()=>{
  const j=job('COMPLETED',{cleanup:{eligible:true}});
  assert.equal(has(j,'cleanup',{fileBusy:true}).enabled,false);
  assert.equal(has(job('COMPLETED',{source_archive:{state:'ARCHIVED'}}),'restore',{fileBusy:true}).enabled,false);
});
check('Recycle recheck uses row identity and only unverified outcomes',()=>{
  assert.equal(has(job('COMPLETED',{source_cleanup:{id:901,state:'RECYCLED',verified:false}}),'recheck').enabled,true);
  assert.equal(has(job('COMPLETED',{source_cleanup:{id:901,state:'RECYCLED',verified:true}}),'recheck'),undefined);
});
check('Audit requires queue and AI readiness',()=>{
  assert.equal(has(job('WAITING_REVIEW'),'audit'),undefined);
  assert.equal(has(job('WAITING_REVIEW',{active_queue_path:'demo'}),'audit',{aiReady:false}).enabled,false);
});
check('Scan rejects empty/unknown detector groups and unsupported OCR batch',()=>{
  const base={detectors:['advertising'],ocr_recognition_batch_size:1,fast_scan:true,content_style:'animation',profile:'careful'};
  assert.deepEqual(C.validateScan(base,true),base);
  assert.throws(()=>C.validateScan({...base,detectors:[]},true));
  assert.throws(()=>C.validateScan({...base,detectors:['invalid']},true));
  assert.throws(()=>C.validateScan({...base,ocr_recognition_batch_size:2},true));
});
check('GB policy uses exact supported modes and numeric boundaries',()=>{
  assert.deepEqual(C.exportSelection('default'),{size_mode:'default'});
  assert.deepEqual(C.exportSelection('unlimited'),{size_mode:'unlimited'});
  assert.deepEqual(C.exportSelection('custom','0.05'),{size_mode:'custom',max_output_gb:.05});
  assert.deepEqual(C.exportSelection('custom',1000),{size_mode:'custom',max_output_gb:1000});
  for(const value of [0,-1,0.049,1001,NaN,Infinity,'garbage'])assert.throws(()=>C.exportSelection('custom',value));
  assert.throws(()=>C.exportSelection('other',2));
});
check('Critical request paths and payloads preserve production contracts',()=>{
  assert.deepEqual(C.request('finalize',job('READY_TO_EXPORT'),{size_mode:'default'}),{operation:'finalize',method:'POST',path:'/api/jobs/42/review/finalize',body:{size_mode:'default'}});
  assert.equal(C.request('stopAfter',job('QUEUED')).path,'/api/jobs/42/stop-after-stage');
  assert.equal(C.request('restore',null,{job_id:42}).path,'/api/source-archive/restore');
  assert.throws(()=>C.request('unknown',job('COMPLETED')));
  assert.throws(()=>C.request('start',null));
});
check('Fixtures do not load production files and expose backend review keys',()=>{
  const fixtureContext={window:{BFContracts:C},structuredClone};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'mock-data.js'),'utf8'),fixtureContext);
  const data=fixtureContext.window.BFMock.create();
  assert.equal(data.jobs.length,15);
  data.jobs.filter(j=>j.review_summary).forEach(j=>{
    assert.ok('main_items' in j.review_summary);assert.ok('decisions' in j.review_summary);
    assert.ok(!('resolved' in j.review_summary));assert.ok(!('total' in j.review_summary));
    assert.match(j.source_sha256,/^[a-f0-9]{64}$/);
  });
  const pending=data.jobs.find(j=>j.id===101);assert.equal(C.reviewStats(pending).remaining,5);
});
check('Prototype has no network transport and enforces connect-src none',()=>{
  const html=fs.readFileSync(path.join(__dirname,'index.html'),'utf8');
  assert.ok(html.includes("connect-src 'none'"));
  for(const file of ['contracts.js','mock-data.js','demo-store.js','download-demo.js','app.js']){
    const text=fs.readFileSync(path.join(__dirname,file),'utf8');
    assert.ok(!/\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource/.test(text),file+' must not connect');
  }
  assert.ok(!/https?:\/\//.test(html),'No remote assets');
});
check('Demo page never loads the adapter; the live page loads no fixture',()=>{
  const demo=fs.readFileSync(path.join(__dirname,'index.html'),'utf8'),live=fs.readFileSync(path.join(__dirname,'live.html'),'utf8');
  assert.ok(!demo.includes('adapter.js')&&demo.includes('demo-store.js')&&demo.includes('mock-data.js'));
  assert.ok(live.includes('data-mode="live"')&&live.includes("connect-src 'self'")&&!live.includes("connect-src 'none'"));
  assert.ok(live.includes('adapter.js')&&!live.includes('mock-data.js')&&!live.includes('demo-store.js'));
  assert.ok(!/https?:\/\//.test(live),'No remote assets');
  const body=t=>t.slice(t.indexOf('<body>')).replace('DỮ LIỆU MẪU','CONTROL CENTER');
  assert.equal(body(live),body(demo),'live.html and index.html share the same body');
});
check('Only adapter.js may use fetch',()=>{
  for(const file of fs.readdirSync(__dirname).filter(f=>/\.js$/.test(f)&&f!=='adapter.js')){
    const text=fs.readFileSync(path.join(__dirname,file),'utf8');
    assert.ok(!/\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource/.test(text),file+' must not connect');
  }
});
check('Demo store keeps the in-memory contract (409 once, no network)',()=>{
  const ctx={window:{BFContracts:C},structuredClone};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'mock-data.js'),'utf8'),ctx);
  const S=require('./demo-store.js').create(C,ctx.window.BFMock);
  const before=S.snapshot().requests.length;S.scenario('conflict');
  return S.dispatch('scheduler',null,{paused:true}).then(()=>assert.fail('409 expected'),e=>{assert.equal(e.status,409);assert.equal(S.snapshot().requests.length,before);});
});
check('Demo store review writes (R2, R3): decision / clear / bulk change the synthetic queue in memory, one at a time, recorded, never sent',async()=>{
  const ctx={window:{BFContracts:C},structuredClone};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'mock-data.js'),'utf8'),ctx);
  const S=require('./demo-store.js').create(C,ctx.window.BFMock),r=S.review(101),q0=await r.queue(),before=S.snapshot().requests.length;
  const target=q0.items.find(x=>!x.decision&&x.suggested_region_source_pixels),plain=q0.items.find(x=>!x.decision&&!x.suggested_region_source_pixels);
  const p1=r.write('decision',{id:target.id,decision:'BLUR',full_frame:false,note:null}),p2=r.write('decision',{id:plain.id,decision:'CUT',full_frame:false,note:null});
  assert.equal(r.pendingWrites(),2);
  const [a,b]=await Promise.all([p1,p2]);
  assert.equal(a.last,false);assert.equal(b.last,true);
  const x=b.body.items.find(i=>i.id===target.id);
  assert.equal(x.decision,'BLUR');assert.equal(JSON.stringify(x.decision_region_source_pixels),JSON.stringify(target.suggested_region_source_pixels));
  assert.equal(b.body.counts.pending,q0.counts.pending-2);
  assert.deepEqual([...S.snapshot().requests.slice(before)].map(d=>d.method+' '+d.path),['POST /api/jobs/101/review/decision','POST /api/jobs/101/review/decision']);
  await assert.rejects(()=>r.write('decision',{id:plain.id,decision:'BLUR',full_frame:false,note:null}),e=>e.status===400,'BLUR needs a region or full_frame');
  const cleared=await r.write('clear',{id:plain.id});
  assert.equal(cleared.body.items.find(i=>i.id===plain.id).decision,null);
  await assert.rejects(()=>r.write('finalize',{size_mode:'default'}),e=>e.status===400,'finalize is not a review write');
  // R3: bulk-keep / bulk-accept as the server selects (S1): undecided main items of the filter; accept skips BLUR without a region.
  const q1=(await r.write('bulkKeep',{filter:'adult'})).body;
  assert.ok(q1.items.filter(x=>x.category==='adult').every(x=>x.decision));
  const undecided=q1.items.filter(x=>!x.decision),skipped=undecided.filter(x=>!['KEEP','BLUR','CUT','NEEDS_MORE_CONTEXT'].includes(x.suggested_decision)||(x.suggested_decision==='BLUR'&&!x.suggested_region_source_pixels));
  const q2=(await r.write('bulkAccept',{filter:'all'})).body;
  assert.equal(q2.items.filter(x=>!x.decision).length,skipped.length);
  await assert.rejects(()=>r.write('bulkKeep',{filter:'visual_ai'}),e=>e.status===400);
  assert.deepEqual([...S.snapshot().requests].slice(-2).map(d=>d.operation+' '+JSON.stringify(d.body)),['bulkKeep {"filter":"adult"}','bulkAccept {"filter":"all"}']);
  S.scenario('offline');
  await assert.rejects(()=>r.write('clear',{id:target.id}),e=>e.status===0);
  assert.equal(r.pendingWrites(),0);
  assert.ok(!/fetch\(|XMLHttpRequest/.test(fs.readFileSync(path.join(__dirname,'demo-store.js'),'utf8')));
});
check('H3 on V2 (the classic "/" page is off since 2026-10-06): the PC says the phone mode is on, never with the code; the phone does not',()=>{
  const on={enabled:true,url:'http://192.168.1.23:8767/',code:'abcd2345'};
  const text=C.phoneNotice(on,{live:true,remote:false});
  assert.equal(text,'Đang mở cho điện thoại: http://192.168.1.23:8767/');
  assert.ok(!text.includes(on.code),'never the code');
  const tail={...on,network:'tailscale',url:'http://100.101.102.103:8767/'};
  assert.equal(C.phoneNotice(tail,{live:true,remote:false}),'Đang mở cho điện thoại qua Tailscale: http://100.101.102.103:8767/');
  assert.equal(C.phoneNotice(tail,{live:true,remote:true}),'','not on the phone itself, over Tailscale either');
  assert.equal(C.phoneNotice(on,{live:true,remote:true}),'','not on the phone itself');
  assert.equal(C.phoneNotice({...on,enabled:false},{live:true,remote:false}),'');
  assert.equal(C.phoneNotice(null,{live:true,remote:false}),'','status not loaded yet');
  assert.equal(C.phoneNotice({unavailable:true},{live:true,remote:false}),'','an older Control Center');
  assert.equal(C.phoneNotice(on,{live:false,remote:false}),'','the demo has no phone mode');
});
check('Cài đặt → Tailscale: the step and its buttons follow GET /api/tailscale; one task at a time; only a Tailscale sign-in link',()=>{
  const base={installed:true,service:'running',backend:'Running',firewall_rule:'enabled',task:null};
  assert.equal(C.tailscaleStep(null),'loading');
  assert.equal(C.tailscaleStep({unavailable:true}),'unavailable');
  assert.equal(C.tailscaleStep({load_error:'x'}),'error');
  assert.equal(C.tailscaleStep({...base,installed:false,service:'missing'}),'install');
  assert.equal(C.tailscaleStep({...base,service:'stopped'}),'service');
  assert.equal(C.tailscaleStep({...base,backend:'NeedsLogin'}),'login');
  assert.equal(C.tailscaleStep({...base,backend:'Stopped'}),'connect');
  assert.equal(C.tailscaleStep(base),'ready');
  const ops=ts=>C.tailscaleActions(ts).map(a=>a[0]);
  assert.deepEqual(ops({...base,installed:false,service:'missing',firewall_rule:'missing'}),['install'],'no firewall button before the install');
  assert.deepEqual(ops({...base,service:'stopped'}),['start-service']);
  assert.deepEqual(ops({...base,backend:'NeedsLogin',firewall_rule:'missing'}),['login','firewall']);
  assert.deepEqual(ops({...base,backend:'Stopped'}),['up','logout']);
  assert.deepEqual(ops(base),['remote-on','down','logout']);
  assert.deepEqual(ops({...base,firewall_rule:'disabled'}),['remote-on','down','logout','firewall']);
  assert.deepEqual(ops({...base,task:{action:'install',running:true}}),[],'one task at a time');
  assert.deepEqual(ops({unavailable:true}),[]);
  assert.deepEqual(C.tailscaleActions(base).map(a=>a[2]),[true,false,false],'the first button is the next step');
  for(const [op,label] of C.tailscaleActions(base))assert.equal(label,C.TAILSCALE_LABELS[op]);
  for(const op of Object.keys(C.tailscaleOps))assert.deepEqual(C.endpoints[C.tailscaleOps[op]],['POST','/api/tailscale/'+op]);
  assert.deepEqual(C.endpoints.phoneExtend,['POST','/api/phone-mode/extend']);
  assert.equal(C.tailscaleLoginUrl({auth_url:'https://login.tailscale.com/a/1b2c3d'}),'https://login.tailscale.com/a/1b2c3d');
  for(const bad of ['https://login.tailscale.com.evil.example/a/1','http://login.tailscale.com/a/1','javascript:alert(1)','https://login.tailscale.com/a/1"onclick=x',null])
    assert.equal(C.tailscaleLoginUrl({auth_url:bad}),null,String(bad));
});
check('Delete flow: "Xóa video" follows the server hint, the golden set and the file lock, on the PC and the phone',()=>{
  const cancelled=job('CANCELLED',{delete:{eligible:true,kind:'CANCELLED',reason:null,size_bytes:5}});
  assert.deepEqual([has(cancelled,'delete').label,has(cancelled,'delete').enabled],['Xóa video',true]);
  assert.equal(has(cancelled,'delete',{aiReady:true,fileBusy:true}).enabled,false);
  // The phone too (2026-10-06): "Xóa video" and "Xóa video gốc" follow the same rules there; "Lưu trữ" stays PC only.
  const phone=has(cancelled,'delete',{aiReady:true,remote:true});
  assert.deepEqual([phone.enabled,phone.reason],[true,'']);
  assert.equal(has(cancelled,'delete',{aiReady:true,remote:true,fileBusy:true}).enabled,false);
  const phoneClean=has(job('COMPLETED',{cleanup:{eligible:true},archive:{eligible:true}}),'cleanup',{aiReady:true,remote:true});
  assert.deepEqual([phoneClean.enabled,phoneClean.reason],[true,'']);
  const phoneArchive=has(job('COMPLETED',{cleanup:{eligible:true},archive:{eligible:true}}),'archive',{aiReady:true,remote:true});
  assert.deepEqual([phoneArchive.enabled,phoneArchive.reason],[false,C.PC_ONLY_REASON]);
  const golden=has({...cancelled,protected:'Video thuộc bộ golden'},'delete');
  assert.deepEqual([golden.enabled,golden.reason],[false,'Video thuộc bộ golden']);
  const refused=has(job('COMPLETED',{source_present:false,delete:{eligible:false,kind:'LOST',reason:'Video gốc còn trong Thùng rác'}}),'delete');
  assert.deepEqual([refused.enabled,refused.reason],[false,'Video gốc còn trong Thùng rác']);
  assert.equal(has(job('COMPLETED'),'delete'),undefined,'no hint, no button');
  const clean=has(job('COMPLETED',{cleanup:{eligible:true},protected:'Video thuộc bộ golden'}),'cleanup');
  assert.deepEqual([clean.label,clean.enabled,clean.reason],['Xóa video gốc',false,'Video thuộc bộ golden']);
  assert.deepEqual(C.permanentOps,['cleanup','delete']);
  assert.deepEqual(C.pcOnlyOps,['archive','restore','recheck']);
  assert.deepEqual(C.endpoints.delete,['POST','/api/job-delete']);
  assert.deepEqual(C.endpoints.deletePreview,['GET','/api/job-delete/preview']);
});
check('Delete flow: "Dọn video mất gốc" lists only eligible lost videos outside the golden set',()=>{
  const lost=(id,extra={})=>({id,state:'COMPLETED',source_present:false,delete:{eligible:true,kind:'LOST'},...extra});
  assert.deepEqual(C.lostIds([lost(9),lost(3),lost(4,{protected:'golden'}),lost(5,{delete:{eligible:false,kind:'LOST'}}),
    {id:6,state:'CANCELLED',delete:{eligible:true,kind:'CANCELLED'}},{id:7,state:'COMPLETED',delete:{eligible:false,kind:null,reason:'x'}},null]),[3,9]);
  assert.deepEqual(C.lostIds(undefined),[]);
});
check('Demo store: permanent deletes remove the video and send confirm_permanent; archive does not',async()=>{
  const ctx={window:{BFContracts:C},structuredClone};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'mock-data.js'),'utf8'),ctx);
  const S=require('./demo-store.js').create(C,ctx.window.BFMock),last=()=>{const r=S.snapshot().requests;return r[r.length-1];};
  const p=await S.preview('delete',[111,113,105]);
  assert.deepEqual(p.eligible.map(x=>[x.job_id,x.kind]),[[111,'CANCELLED'],[113,'LOST']]);
  assert.deepEqual(p.ineligible.map(x=>x.job_id),[105]);
  assert.equal(p.recycle_bin,undefined);
  assert.equal(p.total_bytes,p.eligible[0].size_bytes);
  assert.equal(last().path,'/api/job-delete/preview?ids=111,113,105');
  const r=await S.fileAction('delete',[111,113,105],p.preview_id);
  assert.deepEqual(r.results.map(x=>[x.job_id,x.status]),[[111,'DELETED'],[113,'DELETED']]);
  assert.ok(!S.snapshot().jobs.some(j=>[111,113].includes(j.id)));
  assert.deepEqual([last().method,last().path,last().body.confirm_permanent],['POST','/api/job-delete',true]);
  const c=await S.preview('cleanup',[105]);
  assert.equal(c.recycle_bin,undefined);
  await S.fileAction('cleanup',[105],c.preview_id);
  assert.deepEqual([last().path,last().body.confirm_permanent],['/api/source-cleanup',true]);
  assert.ok(!S.snapshot().jobs.some(j=>j.id===105));
  const a=await S.preview('archive',[109]);
  assert.ok(a.recycle_bin);
  await S.fileAction('archive',[109],a.preview_id);
  assert.deepEqual([last().path,last().body.confirm_permanent],['/api/source-archive',undefined]);
});
check('"Gia hạn": the toast says what was added and the new auto-off time (adds up, at most 24 hours)',()=>{
  const src=fs.readFileSync(path.join(__dirname,'app.js'),'utf8');
  const extendToast=vm.runInNewContext('('+src.match(/function extendToast\(added,after\)\{[\s\S]*?\n\}/)[0]+')');
  const at=1790028800;
  assert.match(extendToast(8*3600,at),/^Đã gia hạn thêm 8 giờ: tự tắt lúc .+\. Mã giữ nguyên\.$/);
  assert.match(extendToast(3*3600+20*60,at),/thêm 3 giờ 20 phút:/);
  assert.match(extendToast(0,at),/^Đã ở mức tối đa 24 giờ: tự tắt lúc /);
  assert.match(extendToast(undefined,at),/^Đã gia hạn: tự tắt lúc /, 'an older Control Center sends no added_seconds');
  assert.equal(extendToast(0,null),'Đã gia hạn. Mã giữ nguyên.');
  assert.match(src,/extendToast\(result&&result\.body&&result\.body\.added_seconds,/, 'the amount comes from the server');
  assert.ok((src.match(/\+EXTEND_NOTE/g)||[]).length===2,'the 24-hour note under both extend buttons');
});
/* Cài đặt → Phần cứng (hardware-view.js, docs/HARDWARE_PROFILE.md 6.1-6.2): GET samples shaped like the contract. */
const HW=require('./hardware-view.js');
const hwEsc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const HW_READY={status:'ready',applies_to_jobs:false,row:{id:'nvidia-sm75-torch2.14.0+cu126',status:'gated',table_version:1},
  families:{fast:{family:'fast',capped:false,logo_decode:'nvdec',tf32:'default',refusal:null},standard_ocr8:{family:'standard_ocr8',capped:false,tf32:'default',refusal:null},
    standard:{family:'standard',capped:false,tf32:'default',refusal:null,warnings:[{code:'c1_off_fp32',text:'Họ fp32 tắt luật máu anime C1.'}]}},
  reasons:[{code:'row_match',text:'RTX 2060 (7.5), torch 2.14.0+cu126, driver 617.42 ≥ 576.80'}],
  warnings:[{code:'nvdec_unverified',text:'Driver NVDEC chưa qua phép tự kiểm: giải mã logo bằng CPU.'}],
  cpu_only:{value:false,valid:true},untested_fast:{value:false,gpu:null,applies:false},
  profile:{present:true,source:'main',written_at:'2026-10-11T09:30:00+07:00',result:'ok',failure:null,power:{battery_present:false,ac_online:true}},
  detection:{gpu:'NVIDIA GeForce RTX 2060',compute_capability:'7.5',vram_mib:6144,driver:'617.42',torch:'2.14.0+cu126',cpu:'CPU mẫu 8 nhân',ram_gib:15.8,
    ffmpeg:{version:'9.0.1',encoders:{libx264:'ok',h264_nvenc:'ok',hevc_nvenc:'ok',av1_nvenc:'failed'},nvdec:{trial:'ok',framemd5_equal_cpu:true},nvdec_codecs:{h264:'ok',hevc:'ok',av1:'not_run'}}},
  task:null,can_remeasure:{ok:false,reason:'not_paused',text:'Hãy tạm dừng hàng đợi trước'}};
const HW_NEEDS={status:'needs_measurement',applies_to_jobs:false,row:null,
  families:{fast:{family:null,refusal:'needs_measurement'},standard_ocr8:{family:null,refusal:'needs_measurement'},standard:{family:null,refusal:'needs_measurement'}},
  reasons:[],warnings:[],cpu_only:{value:false,valid:true},untested_fast:{value:false,gpu:null,applies:false},
  profile:{present:false,source:null,main_state:'missing',last_good_state:'missing'},detection:null,task:null,
  can_remeasure:{ok:true,reason:null,text:''}};
/* hardware_manager: a run of this page's Control Center (BUSY_MESSAGE), and one left by an earlier Control Center
   that the mutex probe could not tell about (PROBE_UNKNOWN_MESSAGE, both in task.message and can_remeasure.text). */
const HW_BUSY='Đang đo phần cứng; chờ lần đo xong rồi bấm lại.';
const HW_PROBE_UNKNOWN='Không kiểm tra được có lần đo nào đang chạy hay không; thử lại sau.';
const HW_RUNNING={...HW_NEEDS,task:{running:true,previous_run:false,step:'torch_probe',index:3,total:9,started_at:'2026-10-11T09:31:00+07:00'},
  can_remeasure:{ok:false,reason:'busy',text:HW_BUSY}};
const HW_UNTESTED={...HW_READY,status:'ready_untested',row:{id:'nvidia-untested',status:'untested',table_version:1},
  families:{fast:{family:'standard',capped:true,tf32:'off',refusal:null},standard_ocr8:{family:'standard',capped:true,tf32:'off',refusal:null},standard:{family:'standard',capped:false,tf32:'off',refusal:null}},
  warnings:[{code:'untested_arch',text:'Chưa qua cổng trên đời card này (sm_86)'},{code:'tf32_off',text:'Tắt TF32: matmul và cuDNN chạy fp32 IEEE (cần cờ dòng lệnh của Đợt 2).'}],
  detection:{...HW_READY.detection,gpu:'NVIDIA GeForce RTX 3060',compute_capability:'8.6',vram_mib:12288},can_remeasure:{ok:true,reason:null,text:''}};
const hwPc=(hardware,extra={})=>({device:'pc',remote:false,offline:false,hardware,...extra});
/* One module over a fake store; ref.s is the snapshot the store hands out (a test changes it between calls) and
   ref.after the one a write leaves behind (the real store reads the panel again after every write). */
function hwModule(ref){
  const calls={toasts:[],modals:[],actions:[],watch:[]};
  const store={snapshot:()=>ref.s,watchHardware:on=>calls.watch.push(on),
    hardwareAction:async(op,enabled)=>{calls.actions.push([op,enabled]);if(ref.after){ref.s=ref.after;ref.after=null;}return {};}};
  const m=HW.create({store,esc:hwEsc,toast:(message,error)=>calls.toasts.push([message,!!error]),showModal:(title,body,commit,label)=>calls.modals.push({title,body,commit,label})});
  return {m,calls};
}
const hwButton=(op,enabled)=>({dataset:enabled===undefined?{op}:{op,enabled}});
check('Cài đặt → Phần cứng: contract endpoints, the module\'s operations, live.html only, app.js wiring under 800 lines',()=>{
  assert.deepEqual([C.endpoints.hardwareProfile,C.endpoints.hardwareRemeasure,C.endpoints.hardwareCpuOnly,C.endpoints.hardwareUntestedFast],
    [['GET','/api/hardware-profile'],['POST','/api/hardware-profile/remeasure'],['POST','/api/hardware-profile/cpu-only'],['POST','/api/hardware-profile/untested-fast']]);
  for(const [op,operation] of Object.entries(HW.OPS))assert.deepEqual(C.endpoints[operation],['POST','/api/hardware-profile/'+op]);
  const live=fs.readFileSync(path.join(__dirname,'live.html'),'utf8'),demo=fs.readFileSync(path.join(__dirname,'index.html'),'utf8');
  assert.ok(live.includes('<script defer src="download-live.js"></script><script defer src="hardware-view.js"></script><script defer src="review-core.js"></script>'));
  assert.ok(!demo.includes('hardware-view.js'),'the demo never loads it');
  const app=fs.readFileSync(path.join(__dirname,'app.js'),'utf8');
  assert.ok(app.split('\n').length<=800,'app.js stays under 800 lines');
  assert.match(app,/const HW=LIVE&&window\.BFHardware\?window\.BFHardware\.create\(\{store,toast:[^}]*esc\}\):null;/);
  assert.match(app,/tailscalePanel\(\)\+\(HW\?HW\.panel\(state\):LIVE\?'':'<section class="panel hardware-panel"[^']*Chỉ có ở bản live[^']*'\)\+/,'the demo says it is live only');
  assert.match(app,/else if\(action==='hw'\)\{if\(HW\)HW\.onAction\(el\);\}/);
  assert.match(app,/if\(HW\)HW\.watch\(view==='settings'\);/);
  assert.match(app,/tailscaleToast\(\);if\(HW\)HW\.onSnapshot\(\);/);
});
check('Phần cứng on the PC (READY): badge, GPU, VRAM, driver, torch, FFmpeg trials, row and families, last measurement; "Đo lại" locked with its reason',()=>{
  const {m}=hwModule({s:hwPc(HW_READY)}),html=m.panel(hwPc(HW_READY));
  assert.match(html,/^<section class="panel hardware-panel" id="hw-panel"/);
  assert.match(html,/<span class="badge hw-status">Sẵn sàng<\/span>/);
  for(const text of ['NVIDIA GeForce RTX 2060','6144 MiB','617.42','2.14.0+cu126','CPU mẫu 8 nhân','15,8 GiB','9.0.1',
    'libx264 đạt · h264_nvenc đạt · hevc_nvenc đạt · av1_nvenc lỗi','h264 đạt · hevc đạt · av1 chưa thử','NVDEC đạt, khung hình giống giải mã CPU',
    'nvidia-sm75-torch2.14.0+cu126 · đã qua cổng · bảng 1','Tăng tốc xử lý</span><span>Tăng tốc xử lý · giải mã logo NVDEC','Họ fp32 tắt luật máu anime C1.',
    hwEsc(HW_READY.reasons[0].text),'Driver NVDEC chưa qua phép tự kiểm','Thành công','Cắm điện · không có pin',HW.NOT_APPLIED])assert.ok(html.includes(text),text);
  assert.ok(html.includes('data-op="remeasure" disabled title="Hãy tạm dừng hàng đợi trước">Đo lại</button>'),'locked with can_remeasure.text');
  assert.ok(html.includes('<p class="muted hw-remeasure-reason">Hãy tạm dừng hàng đợi trước</p>'),'the reason is visible, not only a title');
  assert.ok(html.includes('<button class="secondary" data-action="hw" data-op="cpu-only" data-enabled="1">Chỉ dùng CPU</button>'),'a button, not a checkbox');
  assert.ok(!html.includes('untested-fast')&&!html.includes(HW.UNTESTED_LABEL),'no fast switch on a gated card');
  assert.ok(!/<input/.test(html));
});
check('Phần cứng: NEEDS_MEASUREMENT asks for a first measurement; a running one shows its step and locks "Đo lại"',()=>{
  const {m}=hwModule({s:hwPc(HW_NEEDS)}),needs=m.panel(hwPc(HW_NEEDS));
  assert.match(needs,/<span class="badge blue hw-status">Chưa đo<\/span>/);
  assert.ok(needs.includes('Không cấp: Chưa đo')&&needs.includes('Chưa đo lần nào.'));
  assert.ok(needs.includes('data-op="remeasure">Đo lại</button>'),'"Đo lại" open when can_remeasure.ok');
  assert.ok(!needs.includes('Driver NVIDIA'),'no detection rows before a measurement');
  const running=m.panel(hwPc(HW_RUNNING));
  assert.ok(running.includes('<div class="ts-task hw-task" role="status">'));
  assert.ok(running.includes('Thử torch và CUDA (bước 3/9)')&&running.includes('<div class="meter"><i style="width:33%"></i></div>'));
  assert.ok(running.includes('data-op="remeasure" disabled title="'+HW_BUSY+'">'),'locked with can_remeasure.text, never the panel\'s own words');
  assert.ok(running.includes('<p class="muted hw-remeasure-reason">'+HW_BUSY+'</p>'));
  const failed=m.panel(hwPc({...HW_READY,status:'cuda_unavailable',profile:{present:true,source:'main',written_at:'2026-10-11T09:30:00+07:00',result:'failed',
    failure:{code:'cuda_unavailable',step:'torch_probe',text:'Không dùng được CUDA.',message:'cuda_matmul_failed: lỗi mẫu'},power:{battery_present:false,ac_online:true}}}));
  assert.ok(failed.includes('<span class="badge red hw-status">Không dùng được CUDA</span>'));
  assert.ok(failed.includes('Thất bại: Không dùng được CUDA. · cuda_matmul_failed: lỗi mẫu'),'the user text, then the error');
  const offline=m.panel(hwPc(HW_NEEDS,{offline:true}));
  assert.ok(offline.includes('data-op="remeasure" disabled')&&offline.includes('data-op="cpu-only" data-enabled="1" disabled'),'offline: every button off');
});
check('Phần cứng: the card is untested (READY_UNTESTED): label, warnings and the fast switch; its dialog lists the three risks; body {enabled:true}',async()=>{
  const ref={s:hwPc(HW_UNTESTED)},{m,calls}=hwModule(ref),html=m.panel(ref.s);
  assert.ok(html.includes('<span class="badge amber hw-status">Card chưa qua cổng</span>'));
  assert.ok(html.includes('<span class="badge amber hw-untested" style="white-space:normal">'+HW.UNTESTED_LABEL+'</span>'),'the label next to the row and the families');
  assert.ok(html.includes('Chưa qua cổng trên đời card này (sm_86)')&&html.includes('Tắt TF32: matmul'));
  assert.ok(html.includes('Chuẩn (bị hạ) · TF32 tắt'),'fast and OCR 8 are capped to standard');
  assert.ok(html.includes('data-op="untested-fast" data-enabled="1">Bật chế độ nhanh</button>'));
  m.onAction(hwButton('untested-fast','1'));
  assert.equal(calls.modals.length,1);
  const modal=calls.modals[0];
  assert.equal(modal.title,'Chế độ nhanh trên card chưa qua cổng');
  assert.equal(HW.UNTESTED_FAST_RISKS.length,3);
  for(const risk of HW.UNTESTED_FAST_RISKS)assert.ok(modal.body.includes('<li>'+hwEsc(risk)+'</li>'),risk);
  assert.ok(modal.body.includes('có hiệu lực cho job mới từ Đợt 2')&&modal.body.includes('NVIDIA GeForce RTX 3060 (8.6)'));
  assert.equal(await modal.commit(),true);
  assert.deepEqual(calls.actions,[['hardwareUntestedFast',true]]);
  const on={...HW_UNTESTED,untested_fast:{value:true,gpu:{name:'NVIDIA GeForce RTX 3060',compute_capability:'8.6'},applies:true}};
  const shownOn=m.panel(hwPc(on));
  assert.ok(shownOn.includes('Đang bật cho NVIDIA GeForce RTX 3060 (8.6)')&&shownOn.includes('data-enabled="0">Tắt chế độ nhanh</button>'));
  assert.ok(!shownOn.includes(HW.OTHER_CARD));
  const other={...HW_READY,untested_fast:{value:true,gpu:{name:'NVIDIA GeForce RTX 3060',compute_capability:'8.6'},applies:false}};
  const shownOther=m.panel(hwPc(other));
  assert.ok(shownOther.includes(HW.OTHER_CARD),'switched on for another card');
  assert.ok(shownOther.includes('data-enabled="1" disabled title="Chỉ bật được khi card hiện tại chưa qua cổng.">Bật lại cho card này</button>'));
  assert.ok(shownOther.includes('data-enabled="0">Tắt chế độ nhanh</button>'),'turning it off always works');
  const warned=m.panel(hwPc({...other,status:'ready_untested',warnings:[{code:'untested_fast_other_card',text:HW.OTHER_CARD}]}));
  assert.equal(warned.split(HW.OTHER_CARD).length,2,'the sentence once when the server already warns');
  assert.ok(warned.includes('data-enabled="1">Bật lại cho card này</button>'),'on an untested card it can be bound again');
  ref.s=hwPc(other);m.onAction(hwButton('untested-fast','1'));
  assert.equal(calls.modals.length,1,'no dialog to turn it on while the card is not untested');
  m.onAction(hwButton('untested-fast','0'));
  await calls.modals[1].commit();
  assert.deepEqual(calls.actions[1],['hardwareUntestedFast',false]);
});
check('Phần cứng: the fast switch on for THIS card in a state that never grants it says so, never "card khác"',()=>{
  const {m}=hwModule({s:hwPc(HW_READY)});
  const here={name:'NVIDIA GeForce RTX 2060',compute_capability:'7.5'};
  for(const [name,hw,label] of [['gated now',{...HW_READY,untested_fast:{value:true,gpu:here,applies:false}},'Sẵn sàng'],
    ['CUDA lost',{...HW_READY,status:'cuda_unavailable',untested_fast:{value:true,gpu:here,applies:false}},'Không dùng được CUDA'],
    ['no profile',{...HW_NEEDS,untested_fast:{value:true,gpu:here,applies:false}},'Chưa đo']]){
    const html=m.panel(hwPc(hw));
    assert.ok(html.includes('<p class="muted hw-fast-idle">'+HW.IDLE_SWITCH+' ('+label+').</p>'),name);
    assert.ok(!html.includes(HW.OTHER_CARD)&&!html.includes('Bật lại cho card này'),name+': not another card');
    assert.ok(html.includes('data-enabled="0">Tắt chế độ nhanh</button>'),name+': it can still be turned off');
  }
  const sameNameOtherArch=m.panel(hwPc({...HW_READY,untested_fast:{value:true,gpu:{...here,compute_capability:'8.6'},applies:false}}));
  assert.ok(sameNameOtherArch.includes(HW.OTHER_CARD)&&!sameNameOtherArch.includes(HW.IDLE_SWITCH),'name and compute capability both count');
});
check('Phần cứng: an earlier run the server could not tell about is never "Đang đo"; "Đo lại" keeps can_remeasure.text',()=>{
  const {m}=hwModule({s:hwPc(HW_READY)});
  const previous=message=>({running:true,previous_run:true,step:null,index:null,total:null,started_at:null,...(message===undefined?{}:{message})});
  const unknown=m.panel(hwPc({...HW_READY,task:previous(HW_PROBE_UNKNOWN),can_remeasure:{ok:false,reason:'busy',text:HW_PROBE_UNKNOWN}}));
  assert.ok(unknown.includes('<div class="ts-task hw-task" role="status"><div class="key-value"><span>Đo phần cứng</span><span>'+HW_PROBE_UNKNOWN+'</span></div></div>'));
  assert.ok(!unknown.includes('<span>Đang đo</span>')&&!unknown.includes('class="meter"'),'no claim that a measurement runs');
  assert.ok(unknown.includes('data-op="remeasure" disabled title="'+HW_PROBE_UNKNOWN+'">'),'"Đo lại" locked with the server\'s text');
  const held=m.panel(hwPc({...HW_READY,task:previous('Đang đo (từ lần chạy trước)'),can_remeasure:{ok:false,reason:'busy',text:'Đang đo (từ lần chạy trước)'}}));
  assert.ok(held.includes('<span>Đo phần cứng</span><span>Đang đo (từ lần chạy trước)</span>'),'the server\'s sentence as it is');
  const bare=m.panel(hwPc({...HW_READY,task:previous(),can_remeasure:{ok:false,reason:'busy',text:null}}));
  assert.ok(bare.includes('<span>'+HW.PREVIOUS_UNKNOWN+'</span>')&&!bare.includes('<span>Đang đo</span>'),'no sentence: still no claim');
  assert.ok(bare.includes('data-op="remeasure" disabled title="Chưa đo lại được lúc này.">'));
  assert.ok(!m.panel(hwPc({...HW_READY,task:HW_RUNNING.task,can_remeasure:{ok:false,reason:'busy',text:null}})).includes('Đang đo; đợi'),'never the panel\'s own "running" reason');
});
check('Phần cứng: "Lần đo gần nhất" says "Chưa đo lần nào." only when there is no file; a broken file matches the "Hồ sơ hỏng" badge',()=>{
  const {m}=hwModule({s:hwPc(HW_NEEDS)});
  const absent={present:false,source:null};
  const broken=m.panel(hwPc({...HW_NEEDS,status:'invalid',profile:{...absent,main_state:'invalid',last_good_state:'missing'}}));
  assert.ok(broken.includes('<span class="badge red hw-status">Hồ sơ hỏng</span>'));
  assert.ok(broken.includes('<p class="muted hw-profile-none">'+HW.PROFILE_UNREADABLE+'</p>')&&!broken.includes(HW.NEVER_MEASURED));
  const brokenBackup=m.panel(hwPc({...HW_NEEDS,status:'invalid',profile:{...absent,main_state:'missing',last_good_state:'invalid'}}));
  assert.ok(brokenBackup.includes(HW.PROFILE_UNREADABLE)&&!brokenBackup.includes(HW.NEVER_MEASURED),'a broken last-good copy is a file too');
  const needs=m.panel(hwPc(HW_NEEDS));
  assert.ok(needs.includes('<p class="muted hw-profile-none">'+HW.NEVER_MEASURED+'</p>')&&!needs.includes(HW.PROFILE_UNREADABLE));
  assert.ok(m.panel(hwPc({...HW_NEEDS,profile:{present:false}})).includes(HW.NEVER_MEASURED),'NEEDS_MEASUREMENT without the file states');
  const fallback=m.panel(hwPc({...HW_NEEDS,status:'invalid',message:'Lỗi nội bộ khi đọc hồ sơ phần cứng (KeyError).',profile:absent}));
  assert.ok(!fallback.includes(HW.NEVER_MEASURED)&&!fallback.includes(HW.PROFILE_UNREADABLE)&&fallback.includes('Lỗi nội bộ'),'the fallback answer: its message only');
  const unknownStates=m.panel(hwPc({...HW_NEEDS,status:'invalid',profile:{...absent,main_state:'unknown',last_good_state:'unknown'}}));
  assert.ok(!unknownStates.includes(HW.NEVER_MEASURED)&&!unknownStates.includes(HW.PROFILE_UNREADABLE),'states resolve() could not read');
});
check('Phần cứng: "" on the phone and while the mode is unknown; an older Control Center and a failed first read say so',()=>{
  const {m,calls}=hwModule({s:{device:'phone',remote:true,hardware:HW_READY}});
  assert.equal(m.panel({device:'phone',remote:true,hardware:HW_READY}),'');
  assert.equal(m.panel({device:null,remote:false,hardware:HW_READY}),'','no buttons before /api/phone-mode said PC');
  assert.ok(!m.panel({device:null,remote:false,device_error:'mất kết nối'}).includes('data-action'));
  m.onAction(hwButton('cpu-only','1'));m.onAction(hwButton('remeasure'));
  assert.deepEqual([calls.modals.length,calls.actions.length],[0,0],'the phone never asks');
  assert.match(m.panel(hwPc(null)),/Đang đọc hồ sơ phần cứng/);
  assert.match(m.panel(hwPc({unavailable:true})),/Control Center đang chạy chưa có hồ sơ phần cứng/);
  const failed=m.panel(hwPc({load_error:'Control Center gặp lỗi khi xử lý (500).'}));
  assert.match(failed,/Không đọc được hồ sơ phần cứng: Control Center gặp lỗi/);
  assert.ok(!failed.includes('Chưa đo')&&!failed.includes('data-action'),'a failed read is never "not measured"');
  assert.match(m.panel(hwPc({...HW_READY,load_error:'boom'})),/Lần đọc gần nhất lỗi; đang hiện trạng thái trước đó: boom/);
});
check('Phần cứng: "Chỉ dùng CPU" and "Đo lại" ask first with the contract\'s texts; watch is idempotent',async()=>{
  const ref={s:hwPc(HW_NEEDS)},{m,calls}=hwModule(ref);
  m.onAction(hwButton('cpu-only','1'));
  const cpu=calls.modals[0];
  for(const text of ['Chậm hơn nhiều','Tắt luật C1','Đường CPU chưa qua cổng (E4)','có hiệu lực cho job mới từ Đợt 2','chưa áp dụng trước đó','Job đã xếp giữ thiết bị đã đóng băng'])
    assert.ok(cpu.body.includes(text),text);
  assert.equal(cpu.label,'Bật Chỉ dùng CPU');
  await cpu.commit();
  assert.deepEqual(calls.actions,[['hardwareCpuOnly',true]],'exactly a boolean, never the attribute string');
  m.onAction(hwButton('remeasure'));
  const again=calls.modals[1];
  assert.ok(again.body.includes('dùng GPU vài phút')&&again.body.includes('Hàng đợi phải đang tạm dừng, và stage đang chạy phải xong'));
  await again.commit();
  assert.deepEqual(calls.actions[1],['hardwareRemeasure',undefined]);
  ref.s=hwPc({...HW_READY,cpu_only:{value:true,valid:true}});
  assert.ok(m.panel(ref.s).includes('data-op="cpu-only" data-enabled="0">Tắt Chỉ dùng CPU</button>'));
  m.onAction(hwButton('cpu-only','0'));
  await calls.modals[2].commit();
  assert.deepEqual(calls.actions[2],['hardwareCpuOnly',false]);
  ref.s=hwPc(HW_READY);m.onAction(hwButton('remeasure'));
  assert.equal(calls.modals.length,3,'"Đo lại" locked: no dialog');
  m.watch(true);m.watch(true);m.watch(false);
  assert.deepEqual(calls.watch,[true,false]);
});
check('Phần cứng: the end of a measurement seen running on this page gets one toast; an old result none',async()=>{
  const ref={s:hwPc({...HW_NEEDS,task:{running:false,exit:0,outcome:'done',message:'Đo xong lần trước.',finished_at:'2026-10-10T08:00:00+07:00'}})},{m,calls}=hwModule(ref);
  m.onSnapshot();
  assert.equal(calls.toasts.length,0,'a result from before this page');
  ref.s=hwPc(HW_RUNNING);m.onSnapshot();m.onSnapshot();
  ref.s=hwPc({...HW_READY,task:{running:false,exit:10,outcome:'failed',message:'Lần đo thất bại: không dùng được CUDA.',finished_at:'2026-10-11T09:35:00+07:00'}});
  m.onSnapshot();m.onSnapshot();
  assert.deepEqual(calls.toasts,[['Đo phần cứng: Lần đo thất bại: không dùng được CUDA.',true]]);
  // "Đo lại" that ends before the next poll: the toast still comes, once.
  ref.s=hwPc({...HW_NEEDS});
  m.onAction(hwButton('remeasure'));
  const done={running:false,exit:75,outcome:'busy',message:'Hàng đợi chưa rảnh.',finished_at:'2026-10-11T09:40:00+07:00'};
  ref.after=hwPc({...HW_NEEDS,task:done});
  await calls.modals[0].commit();
  m.onSnapshot();
  assert.deepEqual(calls.toasts.slice(1),[['Đã bắt đầu đo lại phần cứng; khung Phần cứng hiện tiến độ.',false],['Đo phần cứng: Hàng đợi chưa rảnh.',true]]);
});
check('Phần cứng: a run of an earlier Control Center that ends (previous_done, no exit code) is never shown as an error',()=>{
  const ref={s:hwPc({...HW_READY,task:{running:true,previous_run:true,step:null,index:null,total:null,started_at:null,message:'Đang đo (từ lần chạy trước)'}})},{m,calls}=hwModule(ref);
  m.onSnapshot();
  const ended={running:false,previous_run:true,exit:null,outcome:'previous_done',message:'Lần đo của lần chạy trước đã xong.',finished_at:'2026-10-11T09:50:00+07:00'};
  ref.s=hwPc({...HW_READY,task:ended});
  m.onSnapshot();
  assert.deepEqual(calls.toasts,[['Đo phần cứng: Lần đo của lần chạy trước đã xong.',false]]);
  assert.ok(m.panel(ref.s).includes('<p class="muted hw-task">Lần đo gần nhất trên trang: Lần đo của lần chạy trước đã xong.'),'a plain line');
  const unknownExit=m.panel(hwPc({...HW_READY,task:{...ended,outcome:'killed'}}));
  assert.ok(unknownExit.includes('<p class="notice hw-task">'),'any other end without exit 0 stays a notice');
});
check('Phần cứng: every server string is escaped, attributes included',()=>{
  const evil='<img src=x onerror=alert(1)>"\'';
  const bad={...HW_UNTESTED,message:evil,reasons:[{code:'x',text:evil}],warnings:[{code:'y',text:evil}],row:{id:evil,status:'untested',table_version:evil},
    families:{standard:{family:evil,capped:false,tf32:'off',refusal:null,warnings:[{code:'z',text:evil}]}},
    untested_fast:{value:true,gpu:{name:evil,compute_capability:evil},applies:false},
    profile:{present:true,source:'main',written_at:evil,result:evil,failure:{code:'c',message:evil},power:null},
    detection:{gpu:evil,compute_capability:evil,vram_mib:evil,driver:evil,torch:evil,cpu:evil,ram_gib:null,ffmpeg:{version:evil,encoders:{[evil]:evil},nvdec:{trial:evil},nvdec_codecs:{av1:{trial:'not_run',reason:evil}}}},
    task:{running:true,step:evil,index:evil,total:evil},can_remeasure:{ok:false,text:evil}};
  const {m}=hwModule({s:hwPc(bad)}),html=m.panel(hwPc(bad));
  assert.ok(!html.includes('<img'),'no markup from the server');
  assert.ok(!/title="[^"]*"'/.test(html));
  assert.ok(html.includes(hwEsc(evil)));
  assert.ok(!/style="width:NaN%"/.test(html),'a bad progress shows no meter');
});
Promise.all(pending).then(()=>process.stdout.write(JSON.stringify({passed:checks,failed:0})+'\n'),error=>{console.error(error);process.exitCode=1;});
