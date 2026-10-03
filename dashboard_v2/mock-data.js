/* Entirely synthetic fixtures, never imported from reports, state, or input. */
(function(root) {
  'use strict';
  const common = {profile:'careful',content_style:'animation',ocr_recognition_batch_size:1,fast_scan:true,source_present:true,detector_groups:['advertising','adult','gore','violence'],updated_at:'2026-10-03T11:00:00Z',progress:1};
  function job(id,name,state,extra) {
    return {...common,id,job_key:'demo-video-'+id,source_sha256:id.toString(16).padStart(64,'0'),source_path:'E:\\DungChung\\BiliFlow\\input\\'+name+'.mp4',source_size_bytes:245000000,state,active_revision:1,active_queue_path:'DEMO/revision-1/review-queue.json',review_summary:{status:'READY_FOR_EDIT_PLAN',total:5,resolved:5,needs_more_context:0,skip_eligible:false},structure_audit:{result:'PASS',summary:'Queue và phạm vi kiểm tra khớp.'},...extra,name};
  }
  const fixtures = [
    job(101,'Shin · Thú cưng mới của mình đó nha','WAITING_REVIEW',{review_summary:{status:'WAITING_REVIEW',total:8,resolved:3,needs_more_context:1,skip_eligible:false},palette:'sage',duration:'07:26'}),
    job(102,'Nhất Âu Xuân · Tập 31','SCANNING_LOGO',{progress:.64,current_stage:'visual_logo',active_queue_path:null,review_summary:null,structure_audit:null,palette:'rose',content_style:'live_action',duration:'45:12'}),
    job(103,'Shin · Bỏ lỡ tập phim siêu nhân Kamen','READY_TO_EXPORT',{palette:'blue',duration:'07:18'}),
    job(104,'Conan · Bức thư trong căn phòng bí mật','QUEUED',{queue_kind:'scan',queue_position:1,progress:0,active_queue_path:null,review_summary:null,structure_audit:null,palette:'violet',duration:'24:06'}),
    job(105,'Shin · Món thịt ủ tình yêu','COMPLETED',{output_path:'E:\\DungChung\\BiliFlow\\output\\demo-105-reviewed.mp4',cleanup:{eligible:true},archive:{eligible:true},palette:'amber',duration:'07:10'}),
    job(106,'Nhất Âu Xuân · Tập 32','NEEDS_METADATA',{progress:0,active_queue_path:null,review_summary:null,structure_audit:null,detector_groups:[],palette:'rose',content_style:'live_action',duration:'45:03'}),
    job(107,'Shin · Một ngày bình yên','READY_TO_EXPORT',{review_summary:{status:'READY_FOR_EDIT_PLAN',total:0,resolved:0,needs_more_context:0,skip_eligible:true},palette:'sage',duration:'07:12'}),
    job(108,'Conan · Dấu vết còn lại','FAILED',{progress:.37,error:'Không đủ VRAM cho bước hiện tại. Có thể thử lại sau khi GPU trống.',palette:'violet',duration:'24:00'}),
    job(109,'Shin · Quả bom Katsu ca hát','SKIPPED',{review_summary:{status:'READY_FOR_EDIT_PLAN',total:4,resolved:4,needs_more_context:0,skip_eligible:true},cleanup:{eligible:true},archive:{eligible:true},palette:'amber',duration:'01:43:09'}),
    job(110,'Nhất Âu Xuân · Tập 30','COMPLETED',{source_present:false,source_archived:true,source_archive:{id:510,state:'ARCHIVED',kind:'EXPORTED',export_recycled:true,export_verified:false,archived_at:'2026-10-03T09:20:00Z'},palette:'rose',duration:'45:07'}),
    job(111,'Shin · Chuyến đi mùa hè','CANCELLED',{palette:'blue',duration:'07:09'}),
    job(112,'Conan · Hồ sơ cũ','CANCELLED',{hidden_at:'2026-10-03T09:00:00Z',palette:'violet',duration:'24:01'}),
    job(113,'Nhất Âu Xuân · Tập 29','COMPLETED',{source_present:false,source_cleaned:true,source_cleanup:{id:613,state:'RECYCLED',verified:false},palette:'rose',duration:'44:53'}),
    job(114,'Shin · Cùng nhau đi cắm trại','QUEUED',{queue_kind:'export',queue_position:2,palette:'sage',duration:'07:16'}),
    job(115,'Conan · Chuyến tàu đêm','PAUSED',{progress:.28,palette:'violet',duration:'24:02'})
  ];
  const memories = [
    {key:'demo-iqiyi',name:'iQIYI · nhận diện đầu / cuối',memory_class:'platform_logo',platform:'iqiyi',frames:6,color:'sage'},
    {key:'demo-youku',name:'Youku · logo góc trái',memory_class:'platform_logo',platform:'youku',frames:4,color:'amber'},
    {key:'demo-license',name:'Thẻ giấy phép · 国家广播电视总局',memory_class:'studio_logo',frames:3,color:'blue'}
  ];
  root.BFMock = {
    create() {
      const jobs=structuredClone(fixtures).map(j=>{
        if(j.review_summary){
          const r=j.review_summary,blur=r.resolved>0&&!r.skip_eligible?1:0;
          j.review_summary={status:r.status,main_items:r.total,advisory_items:0,pending:r.total-r.resolved-r.needs_more_context,
            decisions:{KEEP:r.resolved-blur,BLUR:blur,NEEDS_MORE_CONTEXT:r.needs_more_context},
            export_size_policy:{mode:'default',maximum_output_gb:3.5},skip_eligible:r.skip_eligible};
        }
        return j;
      });
      return {version:'0.7.24',jobs,logos:structuredClone(memories),memory_sha256:'a'.repeat(64),scheduler_paused:false,source_cleanup_running:false,active:{job_id:102,stage:'visual_logo',pid:12345},queue:{length:2,paused:false},resources:{cpu_percent:38,memory:{percent:54},gpu:{utilization_percent:82,name:'NVIDIA RTX 2060',memory_used_bytes:4487905280,memory_total_bytes:6442450944}},ai:{ready:true,config:{enabled:true,model:'gpt-5.6-luna',reasoning_effort:'medium'},message:'Đã kết nối ChatGPT'},requests:[],scenario:'normal',offline:false};
    },
    scenes(job) {
      const stats=root.BFContracts.reviewStats(job),n=Math.min(stats.total,8);
      return Array.from({length:n},(_,i)=>({id:'scene-'+job.id+'-'+i,start:i*48,end:i*48+12,title:i%3===0?'Logo góc trái · kiểm tra vùng che':i%3===1?'Kiểm tra nội dung cảnh':'Kiểm tra đoạn chuyển cảnh',group:i%3===0?'Quảng cáo / logo':'Ứng viên cần duyệt',decision:i<stats.resolved?'KEEP':i===stats.resolved&&stats.needsMore?'NEEDS_MORE_CONTEXT':null,region:i%3===0?{x:4,y:5,w:19,h:18}:null}));
    }
  };
})(window);
