import { launch, sleep } from './lib.mjs';
const b = await launch('s7'); const p = b.page; const out = {};
try {
  await p.open('#nols');
  out.nolsErr = await p.ev(`window.__nolsErr||null`);
  out.lsThrows = await p.ev(`(()=>{try{window.localStorage;return false}catch(e){return e.name}})()`);
  await p.ev(`selectJobTab('waiting')`);
  out.cardsCount = (await p.cards()).length;
  await p.clickSel('#det-all-46'); await p.clickSel('[data-detector-job="46"][value="advertising"]');
  await sleep(3600); // poll re-render
  out.picker46_after_poll = await p.ev(`[...document.querySelectorAll('[data-detector-job="46"]')].filter(x=>x.checked).map(x=>x.value)`);
  await p.clickSel('button[onclick="start(46,this)"]'); await sleep(1200);
  await p.clickSel('#det-all-47'); await p.clickSel('[data-detector-job="47"][value="advertising"]');
  await p.clickSel('button[onclick="start(47,this)"]'); await sleep(1200);
  await p.clickSel('button[onclick="start(48,this)"]'); await sleep(1200);
  out.posts = await p.ev(`MOCK.posts.map(x=>x.url+' '+JSON.stringify(x.body.detectors))`);
  out.confirms = await p.ev('MOCK.confirms');
  out.notice = await p.ev(`document.getElementById('notice').textContent`);
  out.errors = p.errors();
  // variant: storage present but setItem throws (quota)
  await p.open('');
  await p.ev(`Storage.prototype.setItem=function(){throw new DOMException('full','QuotaExceededError')};0`);
  await p.ev(`selectJobTab('waiting')`);
  await p.clickSel('#det-all-46'); await p.clickSel('[data-detector-job="46"][value="advertising"]');
  await sleep(3600);
  out.quota_picker46 = await p.ev(`[...document.querySelectorAll('[data-detector-job="46"]')].filter(x=>x.checked).map(x=>x.value)`);
  await p.clickSel('button[onclick="start(46,this)"]'); await sleep(1200);
  out.quota_posts = await p.ev(`MOCK.posts.map(x=>x.url+' '+JSON.stringify(x.body.detectors))`);
  out.errors2 = p.errors();
} finally { console.log(JSON.stringify(out, null, 1)); b.close(); process.exit(0) }
