import { launch, sleep } from './lib.mjs';
const b = await launch('s1'); const p = b.page; const out = {};
try {
  await p.open();
  out.tabs = await p.ev(`[...document.querySelectorAll('.job-tab')].map(x=>x.textContent)`);
  out.worker = await p.ev(`document.getElementById('worker').textContent`);
  await p.ev(`selectJobTab('waiting')`);
  out.waiting = await p.cards();
  await p.ev(`selectJobTab('completed')`);
  out.completed = await p.cards();
  out.structure40 = await p.ev(`[...document.querySelectorAll('article.job')].find(a=>a.textContent.includes('#40 '))?.querySelectorAll('.status-box')[1].textContent`);
  // AI audit button on completed job 40
  await p.ev(`selectJobTab('completed')`);
  out.hasAudit40 = await p.ev(`!!document.querySelector('button[onclick="audit(40,true)"]')`);
  await p.clickSel('button[onclick="audit(40,true)"]'); await sleep(200);
  // rerun panel on 40: open it, uncheck all but advertising, rerun
  await p.clickSel('details.rerun-panel[data-job-id="40"] summary'); await sleep(100);
  out.rerunOpen = await p.ev(`document.querySelector('details.rerun-panel[data-job-id="40"]').open`);
  await sleep(3500); // a poll+render happens; panel must stay open
  out.rerunOpenAfterPoll = await p.ev(`document.querySelector('details.rerun-panel[data-job-id="40"]').open`);
  out.rerunPicker40 = await p.ev(`[...document.querySelectorAll('[data-detector-job="40"]')].filter(x=>x.checked).map(x=>x.value)`);
  for (const v of ['adult', 'gore', 'violence']) await p.clickSel(`[data-detector-job="40"][value="${v}"]`);
  await sleep(500);
  await p.clickSel('button[onclick="rerun(40)"]'); await sleep(600);
  // waiting tab actions: resume paused #1, retry failed #5, cancel queued #45, pause queued #44
  await p.ev(`selectJobTab('waiting')`);
  await p.clickSel('button[onclick="act(1,\'resume\')"]'); await sleep(600);
  await p.clickSel('button[onclick="act(5,\'retry\')"]'); await sleep(600);
  await p.clickSel('button[onclick="act(45,\'cancel\')"]'); await sleep(600);
  await p.clickSel('button[onclick="act(44,\'pause\')"]'); await sleep(600);
  await p.clickSel('button[onclick="act(43,\'stop-after-stage\')"]'); await sleep(600);
  // scheduler pause/resume
  await p.ev(`[...document.querySelectorAll('.bar button')].find(b=>b.textContent==='Tạm dừng scheduler').click()`); await sleep(400);
  out.workerPaused = await p.ev(`document.getElementById('worker').textContent`);
  await p.ev(`[...document.querySelectorAll('.bar button')].find(b=>b.textContent==='Chạy hàng đợi').click()`); await sleep(400);
  out.posts = await p.ev(`MOCK.posts.map(x=>x.url+' '+JSON.stringify(x.body))`);
  out.confirms = await p.ev(`MOCK.confirms`); out.alerts = await p.ev(`MOCK.alerts`);
  await p.ev(`selectJobTab('waiting')`);
  out.waitingAfter = await p.cards();
  out.errors = p.errors();
} finally { console.log(JSON.stringify(out, null, 1)); b.close(); process.exit(0) }
