import { launch, sleep } from './lib.mjs';
const b = await launch('s6'); const p = b.page; const out = {};
try {
  await p.open(); await p.ev(`selectJobTab('waiting')`);
  await p.ev(`window.MOCK_POST_DELAY=400;0`);
  await p.ev(`document.querySelector('button[onclick="start(46,this)"]').scrollIntoView({block:'center'})`);
  const r = await p.rect('button[onclick="start(46,this)"]');
  await p.mouse('mouseMoved', r.x, r.y);
  await p.mouse('mousePressed', r.x, r.y, { clickCount: 1 }); await p.mouse('mouseReleased', r.x, r.y, { clickCount: 1 });
  await sleep(120);
  await p.mouse('mousePressed', r.x, r.y, { clickCount: 2 }); await p.mouse('mouseReleased', r.x, r.y, { clickCount: 2 });
  await sleep(2000);
  out.posts = await p.ev(`MOCK.posts.map(x=>x.url+' '+JSON.stringify(x.body.detectors))`);
  out.confirms = await p.ev('MOCK.confirms');
  out.notice = await p.ev(`document.getElementById('notice').className+' | '+document.getElementById('notice').textContent`);
  out.storage = await p.ev(`Object.keys(localStorage)`);
  out.cards = (await p.cards()).filter(x => x.startsWith('#46'));
} finally { console.log(JSON.stringify(out, null, 1)); b.close(); process.exit(0) }
