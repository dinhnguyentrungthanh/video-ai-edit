import { launch, sleep } from './lib.mjs';
const b = await launch('s4'); const p = b.page; const out = {};
try {
  // 1. reload keeps drafts (detectors, style, profile, OCR, speed)
  await p.open(); await p.ev(`selectJobTab('waiting')`);
  await p.clickSel('#det-all-47'); await p.clickSel('[data-detector-job="47"][value="advertising"]');
  await p.ev(`(()=>{for(const [id,v] of [['style-47','mixed'],['profile-47','fast'],['ocr-47','8']]){const s=document.getElementById(id);s.value=v;s.dispatchEvent(new Event('change',{bubbles:true}))}})()`);
  await p.clickSel('#fast-47');
  await sleep(600);
  await p.open(); await p.ev(`selectJobTab('waiting')`);
  out.reload47 = await p.ev(`({det:[...document.querySelectorAll('[data-detector-job="47"]')].filter(x=>x.checked).map(x=>x.value),style:document.getElementById('style-47').value,profile:document.getElementById('profile-47').value,ocr:document.getElementById('ocr-47').value,fast:document.getElementById('fast-47').checked,all:document.getElementById('det-all-47').checked})`);
  // 2. stale slow poll in flight while start(47) => must not resurrect setup card
  await p.ev(`window.MOCK_STATUS_DELAYS=[900,20];window._p=load();0`); await sleep(50);
  await p.clickSel('button[onclick="start(47,this)"]'); await sleep(200);
  out.afterStartFresh = (await p.cards()).filter(x => x.startsWith('#47') || x.startsWith('#48'));
  await sleep(1300);
  out.afterStalePoll = (await p.cards()).filter(x => x.startsWith('#47') || x.startsWith('#48'));
  out.post47 = await p.ev(`JSON.stringify(MOCK.posts.filter(x=>x.url.includes('/47/')).map(x=>x.body))`);
  out.storageAfterStart = await p.ev(`Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)]))`);
  // 3. double-click on Start(48)
  await p.ev(`MOCK.posts.length=0;MOCK.confirms.length=0`);
  await p.ev(`document.querySelector('button[onclick="start(48,this)"]').scrollIntoView({block:'center'})`);
  const r = await p.rect('button[onclick="start(48,this)"]');
  await p.mouse('mouseMoved', r.x, r.y);
  await p.mouse('mousePressed', r.x, r.y, { clickCount: 1 }); await p.mouse('mouseReleased', r.x, r.y, { clickCount: 1 });
  await sleep(90);
  await p.mouse('mousePressed', r.x, r.y, { clickCount: 2 }); await p.mouse('mouseReleased', r.x, r.y, { clickCount: 2 });
  await sleep(1500);
  out.dbl_posts = await p.ev(`MOCK.posts.map(x=>x.url+' '+JSON.stringify(x.body.detectors))`);
  out.dbl_confirms = await p.ev('MOCK.confirms');
  out.dbl_notice = await p.ev(`document.getElementById('notice').className+' | '+document.getElementById('notice').textContent`);
  out.dbl_storage = await p.ev(`Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)]))`);
  out.errors = p.errors();
} finally { console.log(JSON.stringify(out, null, 1)); b.close(); process.exit(0) }
