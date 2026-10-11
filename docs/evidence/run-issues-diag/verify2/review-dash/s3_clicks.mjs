import { launch, sleep } from './lib.mjs';
const b = await launch('s3'); const p = b.page; const out = {};
const renders = `(()=>{if(!window.__rc){window.__rc=0;const o=renderJobs;window.renderJobs=function(f){const before=document.getElementById('jobs').innerHTML.length;o(f);window.__rc++}}})()`;
try {
  await p.open(); await p.ev(`selectJobTab('waiting')`);
  // A: long press (800 ms) on 47's 18+ checkbox while two polls land and the status changes
  await p.ev(`document.querySelector('[data-detector-job="47"][value="adult"]').scrollIntoView({block:'center'})`);
  let r = await p.rect('[data-detector-job="47"][value="adult"]');
  await p.mouse('mouseMoved', r.x, r.y); await p.mouse('mousePressed', r.x, r.y);
  await p.ev(`MOCK.jobs.find(j=>j.id===44).progress=0.5;load()`); await sleep(300);
  await p.ev(`load()`); await sleep(500);
  await p.mouse('mouseReleased', r.x, r.y); await sleep(100);
  out.A_adult47_after_long_press = await p.ev(`document.querySelector('[data-detector-job="47"][value="adult"]').checked`);
  out.A_draft47 = await p.ev(`JSON.stringify(detectorDrafts[47])`);
  await sleep(700);
  out.A_rendered_after_release = await p.ev(`[...document.querySelectorAll('article.job')].find(a=>a.textContent.includes('#44 '))?.querySelector('.progress-value').textContent`);
  // B: click on the label text (not the box) of 47 Máu me, render scheduled right between down/up
  r = await p.ev(`(()=>{const l=document.querySelector('[data-detector-job="47"][value="gore"]').parentElement;const rr=l.getBoundingClientRect();return {x:Math.round(rr.right-8),y:Math.round(rr.y+rr.height/2)}})()`);
  await p.mouse('mouseMoved', r.x, r.y); await p.mouse('mousePressed', r.x, r.y); await p.ev('load()'); await sleep(30); await p.mouse('mouseReleased', r.x, r.y); await sleep(100);
  out.B_gore47_after_label_click = await p.ev(`document.querySelector('[data-detector-job="47"][value="gore"]').checked`);
  // C: native select via mouse: press/release on style-48, then is pointer state released? does polling still render after 1s?
  await p.ev(`document.getElementById('style-48').scrollIntoView({block:'center'})`);
  r = await p.rect('#style-48');
  await p.click(r.x, r.y, 60); await sleep(200);
  out.C_pointerDown_after_select_click = await p.ev('jobsPointerDown');
  out.C_active = await p.ev('document.activeElement.id');
  await p.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40 });
  await p.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  await sleep(200);
  out.C_style48 = await p.ev(`document.getElementById('style-48').value`);
  out.C_meta48 = await p.ev(`JSON.stringify(metadataDrafts[48])`);
  // status change while select focused: does the list update within 3.5 s? (expected: deferred up to 15 s)
  await p.ev(`MOCK.jobs.find(j=>j.id===44).progress=0.7`);
  await sleep(3600);
  out.C_44_progress_while_select_focused_3_6s = await p.ev(`[...document.querySelectorAll('article.job')].find(a=>a.textContent.includes('#44 '))?.querySelector('.progress-value').textContent`);
  await sleep(12500);
  out.C_44_progress_after_16s = await p.ev(`[...document.querySelectorAll('article.job')].find(a=>a.textContent.includes('#44 '))?.querySelector('.progress-value').textContent`);
  out.C_style48_after = await p.ev(`document.getElementById('style-48').value`);
  // D: pointerdown in #jobs then a pointer that never comes up (window blur/alt-tab is simulated by dispatching nothing): is jobsPointerDown capped?
  await p.ev(`document.querySelector('#jobs article .job-key').scrollIntoView({block:'center'})`);
  r = await p.rect('#jobs article .job-key');
  await p.mouse('mouseMoved', r.x, r.y); await p.mouse('mousePressed', r.x, r.y);
  // drag-select text and drop outside the viewport
  await p.mouse('mouseMoved', r.x + 200, r.y + 40); await p.mouse('mouseMoved', 1365, 767);
  await p.mouse('mouseReleased', 1365, 767); await sleep(100);
  out.D_pointerDown_after_drag_release = await p.ev('jobsPointerDown');
  // E: drag a selected text (native DnD) -> pointercancel?
  out.errors = p.errors();
} finally { console.log(JSON.stringify(out, null, 1)); b.close(); process.exit(0) }
