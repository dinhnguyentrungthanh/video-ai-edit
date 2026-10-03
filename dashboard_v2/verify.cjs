'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const C = require('./contracts.js');
const D = require('./download-demo.js');
let checks=0;
function check(name,fn){fn();checks++;process.stdout.write('OK '+name+'\n');}
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
check('Download demo accepts the two requested sources and YouTube short links',()=>{
  assert.equal(D.domains.length,2);
  assert.equal(D.validate('youtube.com',' https://www.youtube.com/watch?v=demo ').domain,'youtube.com');
  assert.equal(D.validate('youtube.com','https://youtu.be/demo').domain,'youtube.com');
  assert.equal(D.validate('phimmoi.example','https://phimmoi.example/phim/video-demo').domain,'phimmoi.example');
});
check('Download demo rejects blank, unsafe, homepage and mismatched links',()=>{
  for(const link of ['', 'not a url','javascript:alert(1)','file:///E:/input/video.mp4','https://youtube.com.evil.example/watch?v=demo','https://evil.example/watch?v=demo','https://user:secret@youtube.com/watch?v=demo','https://www.youtube.com:8443/watch?v=demo','https://www.youtube.com/'])assert.throws(()=>D.validate('youtube.com',link));
  assert.throws(()=>D.validate('phimmoi.example','https://www.youtube.com/watch?v=demo'));
});
check('Hidden applies only to cancelled state',()=>{
  assert.equal(C.hidden(job('CANCELLED',{hidden_at:'x'})),true);
  assert.equal(C.hidden(job('COMPLETED',{hidden_at:'x'})),false);
});
check('Download batches validate atomically and reject duplicate links',()=>{
  const q=D.createQueue(),link=n=>'https://www.youtube.com/watch?v='+n;
  assert.throws(()=>D.enqueue(q,'youtube.com',link(1)+'\nhttps://evil.example/video'));assert.equal(q.items.length,0);assert.equal(q.serial,0);
  assert.throws(()=>D.enqueue(q,'youtube.com',link(1)+'\n'+link(1)));assert.equal(q.items.length,0);
  assert.equal(D.enqueue(q,'youtube.com',link(1)+'\n'+link(2)+'\n'+link(3)),3);
  assert.deepEqual(q.items.map(t=>t.state),['DOWNLOADING','DOWNLOADING','QUEUED']);
  assert.throws(()=>D.enqueue(q,'youtube.com',link(4)+'\n'+link(1)));assert.equal(q.items.length,3);
});
check('Download slots preserve FIFO and verification must finish before completion',()=>{
  const q=D.createQueue();D.setParallel(q,1);D.enqueue(q,'youtube.com',[1,2,3].map(n=>'https://youtu.be/demo'+n).join('\n'));
  for(let i=0;i<8;i++)D.tick(q);assert.equal(q.items[0].state,'VERIFYING');assert.equal(q.items[0].progress,96);assert.equal(q.items[1].state,'QUEUED');
  D.tick(q);assert.equal(q.items[0].state,'COMPLETED');assert.equal(q.items[1].state,'DOWNLOADING');assert.equal(q.items[2].state,'QUEUED');
  D.setParallel(q,3);assert.equal(D.stats(q).active,2);D.setParallel(q,1);assert.equal(D.stats(q).active,2);
  assert.throws(()=>D.setParallel(q,4));assert.equal(q.parallel,1);
});
check('Pause, cancel, failure and retry keep independent task identity',()=>{
  const q=D.createQueue();D.setParallel(q,1);D.enqueue(q,'youtube.com',[1,2].map(n=>'https://youtu.be/demo'+n).join('\n'));
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
check('Unresolved NEEDS_MORE_CONTEXT blocks export even with stale ready status',()=>{
  const j=job('READY_TO_EXPORT',{review_summary:{status:'READY_FOR_EDIT_PLAN',main_items:2,pending:0,decisions:{KEEP:1,NEEDS_MORE_CONTEXT:1}}});
  assert.equal(C.reviewStats(j).remaining,1);assert.equal(has(j,'finalize').enabled,false);
});
check('Unfinished render request blocks resubmission and rerun',()=>{
  const j=job('READY_TO_EXPORT',{render_request:true});
  assert.equal(has(j,'finalize').enabled,false);assert.equal(has(j,'rerun').enabled,false);
});
check('Ready resolved queue accepts export',()=>assert.equal(has(job('READY_TO_EXPORT'),'finalize').enabled,true));
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
  for(const file of ['contracts.js','mock-data.js','download-demo.js','app.js']){
    const text=fs.readFileSync(path.join(__dirname,file),'utf8');
    assert.ok(!/\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource/.test(text),file+' must not connect');
  }
  assert.ok(!/https?:\/\//.test(html),'No remote assets');
});
process.stdout.write(JSON.stringify({passed:checks,failed:0})+'\n');
