import { launch, sleep } from './lib.mjs';
const b = await launch('s8'); const p = b.page; const out = {};
try {
  await p.open(); await p.ev(`selectJobTab('waiting')`);
  await p.ev(`MOCK.confirmAnswer=false;0`);
  await p.clickSel('button[onclick="start(46,this)"]'); await sleep(900);
  out.cancel = await p.ev(`({posts:MOCK.posts.length,confirms:MOCK.confirms,draft:localStorage.getItem('biliflow.jobDraft.46'),scope:localStorage.getItem('biliflow.lastDetectorScope')})`);
  await p.ev(`MOCK.confirmAnswer=true;0`);
  await p.clickSel('button[onclick="start(46,this)"]'); await sleep(900);
  await p.clickSel('button[onclick="start(47,this)"]'); await sleep(900); // same all-groups scope -> no prompt
  out.after = await p.ev(`({posts:MOCK.posts.map(x=>x.url),confirms:MOCK.confirms.length,scope:localStorage.getItem('biliflow.lastDetectorScope'),keys:Object.keys(localStorage)})`);
  out.errors = p.errors();
} finally { console.log(JSON.stringify(out, null, 1)); b.close(); process.exit(0) }
