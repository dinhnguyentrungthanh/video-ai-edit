import { launch, sleep } from './lib.mjs';
const mode = process.argv[2] || 'interleaved';
const lag = +(process.argv[3] || 150);
const b = await launch('s2'); const p = b.page; const out = { mode, lag };
const intent = {}; for (let i = 46; i <= 55; i++) intent[i] = (i % 3 === 0) ? 'all' : 'ads';
try {
  await p.open(); await p.ev(`selectJobTab('waiting')`);
  await p.ev(`window.MOCK_STATUS_DELAYS=Array.from({length:200},(_,i)=>i%4===1?900:(i%4===3?250:30));0`);
  const configure = async id => {
    if (intent[id] === 'ads') {
      await p.clickSel(`#det-all-${id}`); await sleep(lag);
      await p.clickSel(`[data-detector-job="${id}"][value="advertising"]`); await sleep(lag);
    }
    await p.ev(`document.getElementById('style-${id}').scrollIntoView({block:'center'})`);
    // choose live_action with keyboard on focused select (native popup is not scriptable headless)
    await p.ev(`(()=>{const s=document.getElementById('style-${id}');s.focus();s.value='live_action';s.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await sleep(lag);
  };
  const startClick = async id => {
    // user sees the button, reacts after lag, clicks the old coordinates
    await p.ev(`document.querySelector('button[onclick="start(${id},this)"]').scrollIntoView({block:'center'})`);
    const r = await p.rect(`button[onclick="start(${id},this)"]`); await sleep(lag);
    const under = await p.ev(`(()=>{const e=document.elementFromPoint(${r.x},${r.y});return e?(e.getAttribute('onclick')||e.tagName):null})()`);
    await p.click(r.x, r.y, 50); return under;
  };
  out.under = {};
  if (mode === 'interleaved') {
    for (let id = 46; id <= 55; id++) { await configure(id); out.under[id] = await startClick(id); await sleep(lag); }
  } else {
    for (let id = 46; id <= 55; id++) await configure(id);
    out.draftsBefore = await p.ev(`JSON.stringify(detectorDrafts)`);
    for (let id = 46; id <= 55; id++) { out.under[id] = await startClick(id); await sleep(lag); }
  }
  await sleep(3500);
  out.posts = await p.ev(`MOCK.posts.filter(x=>x.url.includes('/start')).map(x=>x.url.match(/\\d+/)[0]+':'+x.body.detectors.join('+')+':'+x.body.content_style)`);
  out.final = await p.ev(`MOCK.jobs.filter(j=>j.id>=46&&j.id<=55).map(j=>j.id+':'+j.state+':'+j.detector_groups.join('+')+':'+j.content_style)`);
  out.expected = Object.entries(intent).map(([k, v]) => k + ':' + (v === 'ads' ? 'advertising' : 'advertising+adult+gore+violence'));
  out.mismatch = out.final.filter((f, i) => !f.startsWith(out.expected[i].split(':')[0] + ':QUEUED:' + out.expected[i].split(':')[1] + ':live_action'));
  out.confirms = await p.ev('MOCK.confirms'); out.notice = await p.ev(`document.getElementById('notice').textContent`);
  out.cards = await p.cards();
  out.storage = await p.ev(`Object.keys(localStorage)`);
  out.errors = p.errors();
} finally { console.log(JSON.stringify(out, null, 1)); b.close(); process.exit(0) }
