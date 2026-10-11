import json
s=json.load(open('../snapshot.json',encoding='utf-8'))
jobs=[]
for j in s['jobs']:
    j=dict(j); i=j['id']
    j['detector_groups']=None
    if i==41: j.update(state='SCANNING_TEXT',current_stage='text',progress=0.2,updated_at='2026-10-02T13:01:20+07:00')
    elif 42<=i<=45:
        t={42:'13:00:50',43:'13:01:08',44:'13:01:17',45:'13:01:24'}[i]
        j.update(state='QUEUED',progress=0,updated_at=f'2026-10-02T{t}+07:00',detector_groups=['advertising'],content_style='live_action')
    elif 46<=i<=50:
        j.update(state='NEEDS_METADATA',content_style='unknown',progress=0,updated_at=j['created_at'],active_queue_path=None)
    elif i==40: j.update(state='COMPLETED',updated_at='2026-10-02T12:55:00+07:00')
    elif i==44: pass
    if j['detector_groups'] is None: j['detector_groups']=['advertising','adult','gore','violence'] if i>=46 else ['advertising']
    jobs.append(j)
mock = """<script>
window.MOCK={jobs:%s,opts:%s,posts:[],confirms:[]};window.confirm=m=>{MOCK.confirms.push(m);return true};
const _origFetch=window.fetch;
window.fetch=async function(url,opt={}){
  const ok=(v)=>new Response(JSON.stringify(v),{status:200,headers:{'Content-Type':'application/json'}});
  if(url==='/api/session')return ok({token:'t'});
  if(url==='/api/ai')return ok({ready:false,config:{},message:'mock'});
  if(url==='/api/status'){const d=(window.MOCK_STATUS_DELAYS||[]).shift()||0;const snap=JSON.parse(JSON.stringify(MOCK.jobs));if(d)await new Promise(r=>setTimeout(r,d));const jobs=snap.slice().sort((a,b)=>(a.priority-b.priority)||(a.updated_at<b.updated_at?1:a.updated_at>b.updated_at?-1:0));
    return ok({jobs:JSON.parse(JSON.stringify(jobs)),detector_options:MOCK.opts,active:{job_id:41,stage:'text'},resources:{}})}
  const m=/^\/api\/jobs\/(\d+)\/start$/.exec(url);
  if(m&&opt.method==='POST'){const body=JSON.parse(opt.body);const id=+m[1];MOCK.posts.push({id,detectors:body.detectors,t:Date.now()});
    const j=MOCK.jobs.find(x=>x.id===id);if(!['NEEDS_METADATA','DISCOVERED'].includes(j.state))return new Response(JSON.stringify({error:'Video #'+id+' state '+j.state}),{status:400,headers:{'Content-Type':'application/json'}});j.state='QUEUED';j.content_style=body.content_style;j.detector_groups=body.detectors;MOCK.n=(MOCK.n||0)+1;j.updated_at='2026-10-02T13:'+String(10+MOCK.n).padStart(2,'0')+':00+07:00';
    if(window.MOCK_POST_DELAY)await new Promise(r=>setTimeout(r,window.MOCK_POST_DELAY));return ok(j)}
  return ok({});
};
</script>
""" % (json.dumps(jobs,ensure_ascii=False,default=str), json.dumps(s['opts'],ensure_ascii=False))
html=open('dashboard_new.html',encoding='utf-8').read()
idx=html.index('<script>')
open('dashboard_mock.html','w',encoding='utf-8').write(html[:idx]+mock+html[idx:])
print('ok')
