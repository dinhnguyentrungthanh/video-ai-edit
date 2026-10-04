/* Entirely synthetic fixtures, never imported from reports, state, or input. */
(function(root) {
  'use strict';
  const common = {profile:'careful',content_style:'animation',ocr_recognition_batch_size:1,fast_scan:true,source_present:true,detector_groups:['advertising','adult','gore','violence'],updated_at:'2026-10-03T11:00:00Z',progress:1};
  function job(id,name,state,extra) {
    return {...common,id,job_key:'demo-video-'+id,source_sha256:id.toString(16).padStart(64,'0'),source_path:'E:\\DungChung\\BiliFlow\\input\\'+name+'.mp4',source_size_bytes:245000000,state,active_revision:1,active_queue_path:'DEMO/revision-1/review-queue.json',review_summary:{status:'READY_FOR_EDIT_PLAN',total:5,resolved:5,needs_more_context:0,skip_eligible:false},structure_audit:{result:'PASS',summary:'Queue và phạm vi kiểm tra khớp.'},...extra,name};
  }
  const fixtures = [
    job(101,'Shin · Thú cưng mới của mình đó nha','WAITING_REVIEW',{review_summary:{status:'WAITING_REVIEW',total:30,resolved:25,needs_more_context:1,skip_eligible:false},palette:'sage',duration:'07:26'}),
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
  /* Review dialog (R0): synthetic queue items in the shape of review_workflow.py. Every kind the
   * dialog must show: scenes with several moments, logos with and without a region, text, a platform
   * logo, an opening check, Visual AI advice, NEEDS_MORE_CONTEXT, advisory candidates, a card without image. */
  const KINDS = [
    {category:'adult',review_kind:'adult',priority:'high',suggested:'CUT',moments:3,label:'Cảnh nhạy cảm'},
    {category:'visual_logo',review_kind:'logo_overlay',priority:'high',suggested:'BLUR',region:[60,40,260,96],label:'Logo góc trái',model:{vlm_confirmation:'CONFIRMED',vlm_answer:'YES | logo góc trái',vlm_source:'qwen'},reason:'Visual brand/logo candidate'},
    {category:'visual_logo',review_kind:'logo_candidate',candidate_type:null,priority:'normal',suggested:'KEEP',label:'Logo toàn khung',boxes:[[120,60,220,90,''],[1500,70,300,100,'covered']],reason:'Full-frame promotional material'},
    {category:'text',review_kind:'in_film_text',priority:'normal',suggested:'KEEP',region:[420,880,1080,120],label:'Chữ trong phim'},
    {category:'gore',review_kind:'gore',priority:'high',suggested:'BLUR',label:'Cảnh máu'},
    {category:'violence',review_kind:'violence',priority:'normal',suggested:'KEEP',moments:2,label:'Cảnh đánh nhau'},
    {category:'text',review_kind:'logo_overlay',priority:'normal',suggested:'BLUR',region:[1500,60,360,110],label:'Chữ quảng cáo góc phải'},
    {category:'visual_logo',review_kind:'platform_logo',candidate_type:'platform_logo',platform_logo:{name:'Nền tảng mẫu'},priority:'high',suggested:'BLUR',region:[1620,940,250,90],label:'Logo nền tảng mẫu'},
    {category:'visual_logo',review_kind:'opening_promotion',candidate_type:'opening_boundary',priority:'context',suggested:null,label:'Đoạn mở đầu',reason:'The first video window is retained once so external intros are not silently missed'},
    {category:'adult',review_kind:'adult',priority:'normal',suggested:'KEEP',ai:{suggested_decision:'BLUR',confidence:0.93,classification:'logo thương hiệu'},label:'Cảnh cần xem lại'}
  ];
  const PALETTES = ['sage','amber','blue','rose','violet'];
  const seconds = text => String(text || '').split(':').reduce((n, part) => n * 60 + (Number(part) || 0), 0);
  function reviewItem(job, i, n, duration, advisory) {
    const k = advisory ? KINDS[[1, 3, 7][i % 3]] : KINDS[i % KINDS.length];
    const start = Math.round((i + 0.5) * duration / (n + 1) * 10) / 10, end = Math.round((start + 4 + (i % 5) * 2) * 10) / 10;
    const moments = k.moments || 1, intervals = Array.from({length: moments}, (_, m) => ({start_seconds: start + m * (end - start) / moments, end_seconds: start + (m + 0.6) * (end - start) / moments}));
    return {id: (advisory ? 'advisory-' : k.category + '-') + job.id + '-' + String(i).padStart(4, '0'), category: k.category, review_kind: k.review_kind,
      candidate_type: k.candidate_type === undefined ? null : k.candidate_type, priority: advisory ? 'context' : k.priority, start_seconds: start, end_seconds: end,
      labels: [k.label], reasons: k.reason ? [k.reason] : [], max_score: Math.round((0.5 + (i % 7) * 0.06) * 1000) / 1000, preview_images: (i % 13 === 12 && !advisory) ? [] : ['demo/poster-' + PALETTES[i % PALETTES.length] + '.svg'],
      suggested_decision: k.suggested, suggested_region_source_pixels: k.region ? {x: k.region[0], y: k.region[1], width: k.region[2], height: k.region[3]} : null,
      source_frame_size: [1920, 1080], temporal_policy: moments > 1 ? 'discrete_detected_intervals' : null, detected_intervals: intervals,
      ...(k.platform_logo ? {platform_logo: k.platform_logo} : {}), ...(k.ai ? {ai_visual_audit: {...k.ai, region_assessment: 'vùng chữ', reasoning: 'Dữ liệu mẫu: chữ trong khung giống logo thương hiệu.'}} : {}), ...(advisory ? {advisory: true} : {}),
      ...(k.model ? {model_evidence: k.model} : {}),
      ...(k.boxes ? {evidence_regions: k.boxes.map(b => ({x: b[0], y: b[1], width: b[2], height: b[3], sources: ['visual_ai'], ...(b[4] ? {covered_by: 'visual_logo-' + job.id + '-0001'} : {})})), evidence_frame_size: [1920, 1080]} : {}),
      decision: null, decision_region_source_pixels: null};
  }
  /* Evidence of one item in the shape of review_evidence.py: ≤24 frames (one "strongest", seeds), seeds with
   * known sample times or only windows (an older scan), detected intervals, the video availability. */
  function reviewEvidence(x, options) {
    options = options || {};
    const start = Number(x.start_seconds), end = Number(x.end_seconds), len = Math.max(.5, end - start), n = options.frames === false ? 0 : (options.count || 12);
    const frames = Array.from({length: n}, (_, i) => ({t: Math.round((start + len * (i + .5) / n) * 1000) / 1000, kind: i === Math.floor(n / 2) ? 'strongest' : i % 3 === 0 ? 'seed' : 'context', score: Math.round((.3 + .04 * i) * 1000) / 1000}));
    const peak = n ? frames[Math.floor(n / 2)] : {t: Math.round((start + len / 2) * 1000) / 1000, score: .81};
    const known = options.known !== false;
    return {frames, strongest: {t: peak.t, score: peak.score}, sample_fps: 2, ignored_ref_count: options.ignored || 0,
      seeds: known ? {known: true, count: 4, threshold: .45, samples: frames.filter(f => f.kind === 'seed').map(f => ({t: f.t, score: f.score}))}
        : {known: false, count: 6, threshold: .45, windows: [{start, end: start + len / 2, count: 6}]},
      detected_intervals: (x.detected_intervals || []).map(d => ({start: d.start_seconds, end: d.end_seconds})),
      context: {extended: [{start: Math.max(0, start - 1), end: end + 1}], threshold: .4},
      video: options.video || {available: true, mime: 'video/webm', reason: null}};
  }
  function reviewQueue(job, options) {
    options = options || {};
    const r = job.review_summary || {main_items: 0, decisions: {}}, d = r.decisions || {}, n = options.count == null ? r.main_items || 0 : options.count;
    const duration = Math.max(60, seconds(job.duration)), items = Array.from({length: n}, (_, i) => reviewItem(job, i, n, duration, false));
    const decisions = options.count == null
      ? [].concat(Array(d.BLUR || 0).fill('BLUR'), Array(d.KEEP || 0).fill('KEEP'), Array(d.CUT || 0).fill('CUT'), Array(d.NEEDS_MORE_CONTEXT || 0).fill('NEEDS_MORE_CONTEXT'))
      : items.map((x, i) => i % 4 === 0 ? null : i % 11 === 5 ? 'NEEDS_MORE_CONTEXT' : x.suggested_decision || 'KEEP');
    let stride = 7; const gcd = (a, b) => b ? gcd(b, a % b) : a; while (n > 1 && gcd(stride, n) !== 1) stride++;
    decisions.forEach((value, k) => {
      const x = items[options.count == null ? (k * stride) % n : k]; if (!x || !value) return;
      x.decision = value; x.decision_region_source_pixels = value === 'BLUR' ? (x.suggested_region_source_pixels || 'FULL_FRAME') : null;
    });
    const advisory = Array.from({length: options.advisory == null ? 3 : options.advisory}, (_, i) => reviewItem(job, i, 3, duration, true));
    const pending = items.filter(x => !x.decision).length, counts = {total: n, pending, decisions: {KEEP: 0, BLUR: 0, CUT: 0, NEEDS_MORE_CONTEXT: 0}};
    items.forEach(x => { if (x.decision) counts.decisions[x.decision]++; });
    const all = ['advertising', 'adult', 'gore', 'violence'], selected = (job.detector_groups || []).slice();
    return {status: counts.decisions.NEEDS_MORE_CONTEXT ? 'NEEDS_MORE_CONTEXT' : pending ? 'REVIEW_REQUIRED' : 'READY_FOR_EDIT_PLAN', counts, items, advisory_items: advisory,
      created_at: '2026-10-03T10:00:00Z', updated_at: '2026-10-03T11:00:00Z', source: {path: job.source_path, sha256: job.source_sha256, duration_seconds: duration},
      reports: ['DEMO/revision-' + job.active_revision + '/report.json'], detection_scope: {selected, skipped: all.filter(x => !selected.includes(x))},
      visual_ai_audit: {assessment_count: items.filter(x => x.ai_visual_audit).length}, export_size_policy: r.export_size_policy || {mode: 'default', maximum_output_gb: 3.5}};
  }
  root.BFMock = {
    reviewQueue,
    reviewEvidence,
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
