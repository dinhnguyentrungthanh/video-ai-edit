import { launch, sleep } from './lib.mjs';
const br = await launch('s5'); const A = br.page; const out = {};
try {
  await A.open('#shared'); await A.ev(`selectJobTab('waiting')`);
  const B = await br.newPage(); await B.open('#shared'); await B.ev(`selectJobTab('waiting')`);
  await A.send('Page.bringToFront');
  // A configures 49 ads-only
  await A.clickSel('#det-all-49'); await A.clickSel('[data-detector-job="49"][value="advertising"]');
  await sleep(3600);
  out.B_picker49_after_poll = await B.ev(`[...document.querySelectorAll('[data-detector-job="49"]')].filter(x=>x.checked).map(x=>x.value)`);
  await B.send('Page.bringToFront'); await sleep(100);
  // B starts 49
  await B.clickSel('button[onclick="start(49,this)"]'); await sleep(400);
  out.B_posts = await B.ev(`MOCK.posts.map(x=>x.url+' '+JSON.stringify(x.body.detectors))`);
  out.A_memory49_after_B_start = await A.ev(`JSON.stringify(detectorDrafts[49]??null)`);
  // A still shows the stale setup card (if its poll has not landed); click Start(49) in A
  out.A_has_start49 = await A.ev(`!!document.querySelector('button[onclick="start(49,this)"]')`);
  await A.send('Page.bringToFront'); await sleep(100);
  if (out.A_has_start49) {
    out.A_picker49_stale = await A.ev(`[...document.querySelectorAll('[data-detector-job="49"]')].filter(x=>x.checked).map(x=>x.value)`);
    await A.clickSel('button[onclick="start(49,this)"]'); await sleep(600);
    out.A_posts = await A.ev(`MOCK.posts.map(x=>x.url+' '+JSON.stringify(x.body.detectors))`);
    out.A_notice = await A.ev(`document.getElementById('notice').textContent`);
    out.A_confirms = await A.ev('MOCK.confirms');
  }
  out.storage = await A.ev(`Object.fromEntries(Object.keys(localStorage).filter(k=>k!=='__mockdb').map(k=>[k,localStorage.getItem(k)]))`);
  out.db49 = await A.ev(`JSON.parse(localStorage.getItem('__mockdb')).filter(j=>j.id===49).map(j=>j.state+':'+j.detector_groups.join('+'))`);
  out.errorsA = A.errors(); out.errorsB = B.errors();
} finally { console.log(JSON.stringify(out, null, 1)); br.close(); process.exit(0) }
