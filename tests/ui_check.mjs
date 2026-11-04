/*
# SPDX-License-Identifier: GPL-3.0-or-later
*/
import fs from 'fs';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
const args = process.argv.slice(2);
const baseUrl = args[0];
const shotsDir = args[1];
if (!baseUrl) { console.error('Usage: node tests/ui_check.mjs <base_url> [shots_dir]'); process.exit(1); }
const candidates = [process.env.CHROME, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', 'google-chrome', 'chromium'].filter(Boolean);
let chromePath = null;
for (const c of candidates) { if (fs.existsSync(c) || !c.includes('/')) { chromePath = c; break; } }
if (!chromePath) { console.log('SKIP no chrome'); process.exit(0); }
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'opos-'));
let chromeProc = null;
const cleanup = () => { try { if (chromeProc) chromeProc.kill('SIGTERM'); } catch {} try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {} };
process.on('exit', cleanup);
process.on('SIGINT', () => { cleanup(); process.exit(1); });
process.on('uncaughtException', (e) => { console.error(e); if (e && e.stack) console.error(e.stack); cleanup(); process.exit(1); });
process.on('unhandledRejection', (e) => { console.error(e); if (e && e.stack) console.error(e.stack); cleanup(); process.exit(1); });
let currentStage = 'startup';
const globalTimeout = setTimeout(() => { console.error(`TIMEOUT after 120s at stage: ${currentStage}`); cleanup(); process.exit(1); }, 120000);
class CdpClient {
  #ws; #pending = new Map(); #nextId = 1; #listeners = new Map();
  constructor(url) { this.#ws = new WebSocket(url); this.#ws.onopen = () => {}; this.#ws.onmessage = e => { try { const m = JSON.parse(e.data); if (m.id && this.#pending.has(m.id)) { const p = this.#pending.get(m.id); this.#pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); } else if (m.method) { const listeners = this.#listeners.get(m.method); if (listeners) { listeners.forEach(cb => { try { cb(m.params); } catch {} }); } } } catch {} }; }
  send(method, params = {}) { return new Promise((res, rej) => { const id = this.#nextId++; this.#pending.set(id, { resolve: res, reject: rej }); this.#ws.send(JSON.stringify({ id, method, params })); }); }
  opened() { if (this.#ws.readyState === WebSocket.OPEN) return Promise.resolve(); return new Promise(resolve => { this.#ws.onopen = resolve; }); }
  close() { try { this.#ws.close(); } catch {} }
  on(method, cb) { if (!this.#listeners.has(method)) this.#listeners.set(method, []); this.#listeners.get(method).push(cb); }
  off(method, cb) { const arr = this.#listeners.get(method); if (!arr) return; const i = arr.indexOf(cb); if (i !== -1) arr.splice(i, 1); if (arr.length === 0) this.#listeners.delete(method); }
}
const spawnChrome = () => new Promise((res, rej) => {
  chromeProc = spawn(chromePath, ['--headless=new','--remote-debugging-port=0','--user-data-dir='+tmpDir,'--no-first-run','--window-size=360,780','--disable-gpu'], { stdio: ['ignore','ignore','pipe'] });
  let out = '';
  chromeProc.stderr.on('data', d => { out += d.toString(); const m = out.match(/DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)/); if (m) { res(m[1]); } });
  chromeProc.on('error', rej);
  setTimeout(() => rej(new Error('chrome start timeout')), 15000);
});
let apiToken;
const apiPost = async (obj) => { if (!apiToken) { const r = await fetch(baseUrl + '/'); const m = (await r.text()).match(/name="opos-token" content="([^"]+)"/); apiToken = m[1]; } await fetch(baseUrl + '/api', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-OPOS-Token': apiToken }, body: JSON.stringify(obj) }); };
const waitTone = async (cdp) => { for (let i = 0; i < 50; i++) { const v = await cdp.send('Runtime.evaluate', { expression: "document.querySelector('#meter')?.dataset.tone", returnByValue: true, awaitPromise: true }); if (v.result?.value) return true; await new Promise(r => setTimeout(r, 100)); } return false; };
const evalJs = async (cdp, expr) => { const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails)); return r.result?.value; };
const ready = cdp => evalJs(cdp, "(async()=>{oposUI.unlock();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))})()");
const mouse = async (cdp, type, x, y) => { await cdp.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }); };
const key = async (cdp, type, keyName) => { await cdp.send('Input.dispatchKeyEvent', { type, key: keyName, code: keyName, keyCode: 0, windowsVirtualKeyCode: 0 }); };
let pageEnabled = false;
const waitForLoadEvent = (cdp, timeoutMs = 45000) => new Promise((resolve, reject) => {
  const handler = () => { clearTimeout(timer); cdp.off('Page.loadEventFired', handler); resolve(); };
  const timer = setTimeout(() => { cdp.off('Page.loadEventFired', handler); reject(new Error('load timeout')); }, timeoutMs);
  cdp.on('Page.loadEventFired', handler);
});
const reloadAndWait = async (cdp) => {
  if (!pageEnabled) { await cdp.send('Page.enable'); pageEnabled = true; }
  const loadPromise = waitForLoadEvent(cdp, 45000);
  loadPromise.catch(() => {});
  await cdp.send('Page.reload');
  await loadPromise;
  const start = Date.now();
  while (Date.now() - start < 5000) {
    const width = await evalJs(cdp, 'document.querySelector("#meter-fill")?.style.width');
    if (width) { await ready(cdp); return; }
    await new Promise(r => setTimeout(r, 50));
  }
  throw new Error('render timeout');
};
(async () => {
  try {
    currentStage = 'startup: spawning chrome';
    const port = await spawnChrome();
    const target = await fetch(`http://127.0.0.1:${port}/json/new?url=${encodeURIComponent(baseUrl)}`, { method: 'PUT' }).then(r => r.json());
    const cdp = new CdpClient(target.webSocketDebuggerUrl);
    await cdp.opened();
    await cdp.send('Page.enable');
    pageEnabled = true;
    currentStage = 'startup: initial page load';
    const initialLoad = waitForLoadEvent(cdp, 45000);
    initialLoad.catch(() => {});
    await cdp.send('Page.navigate', { url: baseUrl });
    await initialLoad;
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 360, height: 780, deviceScaleFactor: 2, mobile: true });
    await reloadAndWait(cdp);
    const results = [];
    const check = async (name, fn) => { currentStage = `test: ${name}`; try { await fn(); results.push(`PASS ${name}`); } catch (e) { results.push(`FAIL ${name}: ${e.message}`); } };
    await check('meter width', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      const ratio = await evalJs(cdp, `(() => { const m = document.querySelector('#meter'); const f = document.querySelector('#meter-fill'); return f.getBoundingClientRect().width / m.getBoundingClientRect().width; })()`);
      if (Math.abs(ratio - 0.42) > 0.02) throw new Error(`ratio ${ratio}`);
    });
    await check('title shows percent', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      const text = await evalJs(cdp, "document.querySelector('#sheet-title').textContent");
      if (text !== '42%') throw new Error(`title ${text}`);
      const label = await evalJs(cdp, "document.querySelector('#sheet-title').getAttribute('aria-label')");
      if (!/Battery 42 percent/.test(label || '')) throw new Error(`aria-label ${label}`);
    });
    await check('tone and theme colours', async () => {
      for (const theme of ['light', 'dark']) {
        await evalJs(cdp, `document.documentElement.dataset.theme=${JSON.stringify(theme)}`);
        for (const [level, tone, charging] of [[19,'low',false],[20,'normal',false],[100,'full',false],[50,'charging',true],[100,'full',true]]) {
          await apiPost({ action: 'charge', on: charging });
          await apiPost({ action: 'battery', level });
          await reloadAndWait(cdp);
          await evalJs(cdp, `document.documentElement.dataset.theme=${JSON.stringify(theme)}`);
          const v = await evalJs(cdp, `(()=>{const m=document.querySelector('#meter'), f=document.querySelector('#meter-fill'), s=getComputedStyle(f), root=getComputedStyle(document.documentElement);return {tone:m.dataset.tone,title:document.querySelector('#sheet-title').dataset.tone,fill:s.backgroundColor,shadow:s.boxShadow,animation:getComputedStyle(m).animationName,fg:root.getPropertyValue('--jaryo-fg').trim(),bg:getComputedStyle(m).backgroundColor}})()`);
          const expected = theme === 'light' ? 'rgb(26, 26, 26)' : 'rgb(230, 226, 217)';
          if (v.tone !== tone || v.title !== tone) throw new Error(`tone ${JSON.stringify(v)} != ${tone}`);
          if (v.fill !== expected || v.fill === v.bg || v.shadow !== 'none' || v.animation !== 'none') throw new Error(`theme ${theme}: ${JSON.stringify(v)}`);
        }
      }
      await apiPost({ action: 'charge', on: false });
      await apiPost({ action: 'battery', level: 42 });
      await reloadAndWait(cdp);
    });
    await check('tap toggle', async () => {
      const rect = await evalJs(cdp, "(() => { const r = document.querySelector('#meter-hit').getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; })()");
      await mouse(cdp, 'mousePressed', rect.x, rect.y); await mouse(cdp, 'mouseReleased', rect.x, rect.y);
      await new Promise(r=>setTimeout(r,450));
      if (!await evalJs(cdp, 'window.opos.isOpen()')) throw new Error('not open');
      if (await evalJs(cdp, "document.querySelector('#meter').getAttribute('aria-expanded')") !== 'true') throw new Error('aria-expanded');
      await new Promise(r=>setTimeout(r,150));
      if (await evalJs(cdp, "document.activeElement.id") !== 'sheet-title') throw new Error('focus');
      const dismiss = await evalJs(cdp, "(()=>{const r=document.querySelector('#sheet-handle').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()");
      await mouse(cdp, 'mousePressed', dismiss.x, dismiss.y); await mouse(cdp, 'mouseReleased', dismiss.x, dismiss.y);
      if (await evalJs(cdp, 'window.opos.isOpen()')) throw new Error('still open');
      await new Promise(r=>setTimeout(r,450));
      if (await evalJs(cdp, "document.activeElement.id") !== 'meter') throw new Error('focus back');
    });
    await check('drag open/close', async () => {
      const start = await evalJs(cdp, "(() => { const r = document.querySelector('#meter-hit').getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; })()");
      await mouse(cdp, 'mousePressed', start.x, start.y);
      for (let i = 1; i <= 10; i++) { const y = start.y + (500 - start.y) * i / 10; await mouse(cdp, 'mouseMoved', start.x, y); if (i === 5) { const bottom = await evalJs(cdp, "document.querySelector('#sheet').getBoundingClientRect().bottom"); if (bottom < 60 || bottom > 500) throw new Error(`bottom ${bottom}`); } }
      await mouse(cdp, 'mouseReleased', start.x, 500);
      await new Promise(r=>setTimeout(r,450));
      if (!await evalJs(cdp, 'window.opos.isOpen()')) throw new Error('drag open fail');
      const closeStart = await evalJs(cdp, "(() => { const r = document.querySelector('#time-left').getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; })()");
      await mouse(cdp, 'mousePressed', closeStart.x, closeStart.y);
      for (let i = 1; i <= 10; i++) { const y = closeStart.y + (0 - closeStart.y) * i / 10; await mouse(cdp, 'mouseMoved', closeStart.x, y); }
      await mouse(cdp, 'mouseReleased', closeStart.x, 0);
      if (await evalJs(cdp, 'window.opos.isOpen()')) throw new Error('drag close fail');
      await new Promise(r=>setTimeout(r,450));
      const handle = await evalJs(cdp, "(() => { const r = document.querySelector('#meter-hit').getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; })()");
      await mouse(cdp, 'mousePressed', handle.x, handle.y); await mouse(cdp, 'mouseMoved', handle.x, handle.y + 20); await mouse(cdp, 'mouseReleased', handle.x, handle.y + 20);
      if (await evalJs(cdp, 'window.opos.isOpen()')) throw new Error('small drag opened');
      await new Promise(r=>setTimeout(r,450));
    });
    await check('keyboard', async () => {
      await evalJs(cdp, "document.querySelector('#meter').focus()");
      await key(cdp, 'keyDown', 'ArrowDown'); await key(cdp, 'keyUp', 'ArrowDown');
      await new Promise(r=>setTimeout(r,450));
      if (!await evalJs(cdp, 'window.opos.isOpen()')) throw new Error('arrow down');
      await key(cdp, 'keyDown', 'Escape'); await key(cdp, 'keyUp', 'Escape');
      if (await evalJs(cdp, 'window.opos.isOpen()')) throw new Error('escape');
      if (await evalJs(cdp, "document.activeElement.id") !== 'meter') throw new Error('focus meter');
      await new Promise(r=>setTimeout(r,450));
    });
    await check('aria', async () => {
      const ok = await evalJs(cdp, `(() => {
        const m = document.querySelector('#meter');
        if (m.tagName !== 'BUTTON' || m.type !== 'button') return false;
        if (!/Battery/i.test(m.getAttribute('aria-label')||'')) return false;
        if (m.getAttribute('aria-controls') !== 'sheet') return false;
        const s = document.querySelector('#sheet');
        if (s.getAttribute('role') !== 'dialog') return false;
        if (s.getAttribute('aria-modal') !== 'true') return false;
        const pausedBtns = [...document.querySelectorAll('#paused-list button')];
        if (pausedBtns.some(b => !b.getAttribute('aria-label')?.trim())) return false;
        const sheetBtns = [...document.querySelectorAll('#sheet button')];
        if (sheetBtns.some(b => b.getBoundingClientRect().height < 44)) return false;
        return true;
      })()`);
      if (!ok) throw new Error('aria fail');
    });
    await check('contrast', async () => {
      await evalJs(cdp, "document.querySelector('#meter').click()");
      await new Promise(r => setTimeout(r, 300));
      const ratios = await evalJs(cdp, `(() => {
        const lum = c => { const v = c/255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
        const contrast = (r1,g1,b1,r2,g2,b2) => { const L1 = 0.2126*lum(r1)+0.7152*lum(g1)+0.0722*lum(b1); const L2 = 0.2126*lum(r2)+0.7152*lum(g2)+0.0722*lum(b2); const [a,b] = L1>L2?[L1,L2]:[L2,L1]; return (a+0.05)/(b+0.05); };
        const getBg = el => { let p = el.parentElement; while(p){ const s = getComputedStyle(p); const bg = s.backgroundColor; if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') { const m = bg.match(/\\d+/g); if (m && m.length>=3) return [+m[0],+m[1],+m[2]]; } p = p.parentElement; } return [255,255,255]; };
        const check = sel => { const e = document.querySelector(sel); const s = getComputedStyle(e); const m = s.color.match(/\\d+/g); if (!m) return 0; const [r,g,b] = [+m[0],+m[1],+m[2]]; const bg = getBg(e); return contrast(r,g,b,bg[0],bg[1],bg[2]); };
        return { title: check('#sheet-title'), time: check('#time-left'), head: check('#headline') };
      })()`);
      if (ratios.title < 3.0) throw new Error('title contrast');
      if (ratios.time < 4.5) throw new Error('time contrast');
      if (ratios.head < 4.5) throw new Error('headline contrast');
    });
    await check('quiet', async () => {
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 200));
      const ok = await evalJs(cdp, `(() => {
        const sheet = document.querySelector('#sheet');
        const nodes = [...document.querySelectorAll('body *')].filter(n => !sheet.contains(n));
        const bad = /\\b(AI|model|governor|assistant)\\b/i;
        if (nodes.some(n => bad.test(n.innerText))) return false;
        const screen = document.querySelector('#screen');
        if (screen && /%/.test(screen.innerText)) return false;
        return true;
      })()`);
      if (!ok) throw new Error('quiet fail');
    });
    await check('strip visible', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 450));
      const v = await evalJs(cdp, "(() => { const m = document.querySelector('#meter'); const r = m.getBoundingClientRect(); const f = document.querySelector('#meter-fill'); const fr = f.getBoundingClientRect(); return { top: r.top, left: r.left, width: r.width, inner: window.innerWidth, height: r.height, fillBg: getComputedStyle(f).backgroundColor, meterBg: getComputedStyle(m).backgroundColor, fillW: fr.width }; })()");
      if (v.top !== 0) throw new Error(`top ${v.top}`);
      if (v.left !== 0) throw new Error(`left ${v.left}`);
      if (v.width !== v.inner) throw new Error(`width ${v.width} != ${v.inner}`);
      if (!(v.height >= 3 && v.height <= 5)) throw new Error(`height ${v.height}`);
      if (v.fillBg === v.meterBg) throw new Error(`fill same as meter ${v.fillBg}`);
      if (!(v.fillW > 0)) throw new Error(`fill width ${v.fillW}`);
    });
    await check('no battery text on phone', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 450));
      const v = await evalJs(cdp, "(() => { const s = document.querySelector('#screen'); const screenText = s ? s.innerText : null; const firstLine = s ? s.firstChild?.textContent.trim() : null; let statusText = null; let statusVisible = false; const st = document.querySelector('#status'); if (st) { const r = st.getBoundingClientRect(); const cs = getComputedStyle(st); statusVisible = r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden'; statusText = st.innerText; } const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let outsideText='',n;while(n=walker.nextNode()){const e=n.parentElement;if(!e||e.closest('#sheet,#demo,#sb,script,style'))continue;const r=document.createRange();r.selectNodeContents(n);if(r.getClientRects().length&&getComputedStyle(e).visibility==='visible')outsideText+=n.textContent} return { screenText, firstLine, statusText, statusVisible, outsideText }; })()");
      if (v.screenText == null) throw new Error('no screen');
      if (v.screenText.includes('Battery')) throw new Error('screen Battery');
      if (v.screenText.includes('%')) throw new Error('screen %');
      if (v.statusVisible) { if ((v.statusText || '').includes('Battery')) throw new Error('status Battery'); if ((v.statusText || '').includes('%')) throw new Error('status %'); }
      if (v.outsideText.includes('Battery')) throw new Error('outside Battery');
      if (v.outsideText.includes('%')) throw new Error('outside %');
      if (v.firstLine !== 'Calls') throw new Error(`first line ${v.firstLine}`);
    });
    await check('demo panel hidden by default', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 450));
      const init = await evalJs(cdp, "(() => { const d = document.querySelector('#demo'); const b = document.querySelector('#battery'); return { display: d ? getComputedStyle(d).display : null, hasOffset: !!(b && b.offsetParent) }; })()");
      if (init.display !== 'none') throw new Error(`demo display ${init.display}`);
      if (init.hasOffset) throw new Error('battery visible');
      const p = waitForLoadEvent(cdp, 45000); p.catch(() => {}); await cdp.send('Page.navigate', { url: baseUrl + '/?demo=1' }); await p;
      const shown = await evalJs(cdp, "(() => { const d = document.querySelector('#demo'); const b = document.querySelector('#battery'); return { display: d ? getComputedStyle(d).display : null, hasOffset: !!(b && b.offsetParent) }; })()");
      if (shown.display === 'none') throw new Error('demo not shown');
      if (!shown.hasOffset) throw new Error('battery no offsetParent');
      const q = waitForLoadEvent(cdp, 45000); q.catch(() => {}); await cdp.send('Page.navigate', { url: baseUrl }); await q;
      await ready(cdp);
      await waitTone(cdp);
    });
    await check('sheet covers inputs', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 450));
      await evalJs(cdp, "window.opos.open()");
      await new Promise(r => setTimeout(r, 450));
      const v = await evalJs(cdp, "(() => { const sheet = document.querySelector('#sheet'); const r = sheet.getBoundingClientRect(); const bg = getComputedStyle(sheet).backgroundColor; let alpha = 1; const m = bg.match(/rgba?\\(([^)]+)\\)/); if (m) { const parts = m[1].split(',').map(s => s.trim()); if (parts.length === 4) alpha = parseFloat(parts[3]); } const zSheet = getComputedStyle(sheet).zIndex; const zMeter = getComputedStyle(document.querySelector('#meter')).zIndex; const sels = ['#number', '#msg', '#controls button']; const items = sels.map(sel => { const el = document.querySelector(sel); if (!el) return { sel, found: false }; const er = el.getBoundingClientRect(); const cx = er.left + er.width / 2; const cy = er.top + er.height / 2; const inView = cx >= 0 && cx < window.innerWidth && cy >= 0 && cy < window.innerHeight; if (!inView) return { sel, found: true, inView: false }; const ep = document.elementFromPoint(cx, cy); if (!ep) return { sel, found: true, inView: true, cover: 'none' }; if (ep === el) return { sel, found: true, inView: true, cover: 'self' }; if (ep.id === 'scrim' || (ep.closest && ep.closest('#scrim'))) return { sel, found: true, inView: true, cover: 'scrim' }; if (ep.closest && ep.closest('#sheet')) return { sel, found: true, inView: true, cover: 'sheet' }; return { sel, found: true, inView: true, cover: 'other:' + (ep.tagName + '#' + ep.id + '.' + ep.className) }; }); return { height: r.height, inner: window.innerHeight, alpha, bg, zSheet, zMeter, items }; })()");
      if (!(v.height >= 0.85 * v.inner)) throw new Error(`sheet height ${v.height} < ${0.85 * v.inner}`);
      if (v.alpha !== 1) throw new Error(`sheet bg alpha ${v.bg}`);
      for (const it of v.items) { if (!it.found) throw new Error(`${it.sel} missing`); if (it.inView && it.cover !== 'sheet' && it.cover !== 'scrim') throw new Error(`${it.sel} not covered: ${it.cover}`); }
      if (!(Number(v.zSheet) > Number(v.zMeter))) throw new Error(`z ${v.zSheet} <= ${v.zMeter}`);
    });
    await check('sheet content fits', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 450));
      await apiPost({ action: 'goal', time: '23:00' });
      await reloadAndWait(cdp); await waitTone(cdp);
      await evalJs(cdp, "window.opos.open()");
      await new Promise(r => setTimeout(r, 450));
      const v = await evalJs(cdp, "(() => { const sheet = document.querySelector('#sheet'); const details=document.querySelector('#shade-technical'); if(details.open)throw new Error('technical details must start collapsed'); details.open=true; sheet.scrollTop = sheet.scrollHeight; const rows = [...sheet.querySelectorAll('.feature-row')]; const last = rows[rows.length - 1]; const lr = last ? last.getBoundingClientRect() : null; const grip = document.querySelector('#sheet-grip'); const gr = grip ? grip.getBoundingClientRect() : null; const sheetText = sheet.innerText || ''; const count = (sheetText.match(/Saving power for/g) || []).length; const hl = document.querySelector('#history-label'); const g = document.querySelector('#goal'); const gb = g ? getComputedStyle(g).backgroundColor : null; const btn = document.querySelector('#goal-row button'); const bb = btn ? getComputedStyle(btn).backgroundColor : null; return { bottom: lr ? lr.bottom : null, gripTop: gr ? gr.top : null, count, history: hl ? hl.textContent.trim() : null, goalBg: gb, btnBg: bb, themeBg: getComputedStyle(sheet).backgroundColor }; })()");
      const parse = s => { const m = (s || '').match(/\d+/g); return m ? [+m[0], +m[1], +m[2]] : null; };
      if (!(v.bottom != null && v.gripTop != null && v.bottom <= v.gripTop + 1)) throw new Error(`overflow ${v.bottom} > ${v.gripTop}`);
      if (v.count !== 1) throw new Error(`count ${v.count}`);
      if (v.history !== 'last hour') throw new Error(`history ${v.history}`);
      const gb = parse(v.goalBg);
      if (!gb || v.goalBg !== v.themeBg) throw new Error(`goal bg ${v.goalBg}`);
      const bb = parse(v.btnBg);
      if (!bb || v.btnBg !== v.themeBg) throw new Error(`btn bg ${v.btnBg}`);
      await apiPost({ action: 'goal', time: '' });
    });
    await check('pull cue', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 450));
      const v = await evalJs(cdp, "(() => { const hit = document.querySelector('#meter-hit'); const w = hit ? getComputedStyle(hit, '::after').width : null; return { width: w, grip: !!document.querySelector('#sheet-grip') }; })()");
      if (v.width !== '40px') throw new Error(`after width ${v.width}`);
      if (!v.grip) throw new Error('no grip');
    });
    await check('call state and error line', async () => {
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
      await apiPost({ action: 'call', number: '+15550100' }); await reloadAndWait(cdp); await waitTone(cdp);
      const v = await evalJs(cdp, "(() => { const n = document.querySelector('#notice'); const phone = document.querySelector('#phone'); const demo = document.querySelector('#demo'); const cs = n ? getComputedStyle(n) : null; return { text: n ? n.textContent : null, visible: !!n && cs.display !== 'none' && cs.visibility !== 'hidden' && n.offsetHeight > 0, inPhone: !!(n && phone && phone.contains(n)), inDemo: !!(n && demo && demo.contains(n)), fontSize: cs ? parseFloat(cs.fontSize) : null }; })()");
      if (!v.visible) throw new Error('notice not visible');
      if (v.text !== 'In call') throw new Error(`notice ${v.text}`);
      if (!v.inPhone) throw new Error('notice not in phone');
      if (v.inDemo) throw new Error('notice in demo');
      if (!(v.fontSize < 16)) throw new Error(`font-size ${v.fontSize}`);
      if (/battery|AI|model|governor|assistant/i.test(v.text || '')) throw new Error('notice bad text');
      await apiPost({ action: 'hang_up' }); await apiPost({ action: 'call', number: 'abc' }); await reloadAndWait(cdp); await waitTone(cdp);
      const e = await evalJs(cdp, "(() => { const n = document.querySelector('#notice'); const cs = n ? getComputedStyle(n) : null; return { text: n ? n.textContent : null, visible: !!n && cs.display !== 'none' && cs.visibility !== 'hidden' && n.offsetHeight > 0 }; })()");
      if (!e.visible) throw new Error('error not visible');
      if (e.text !== 'Number not valid') throw new Error(`error ${e.text}`);
      await apiPost({ action: 'hang_up' }); await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp); await waitTone(cdp);
    });
    await check('screen legible when dim', async () => {
      await apiPost({ action: 'battery', level: 5 }); await reloadAndWait(cdp);
      // Page brightness is an owner preference; low battery must not dim text twice.
      await evalJs(cdp, "(()=>{const e=document.querySelector('#bright');e.value='60';e.dispatchEvent(new Event('input',{bubbles:true}))})()");
      const filters = await evalJs(cdp, "({page:getComputedStyle(document.documentElement).filter,screen:getComputedStyle(document.querySelector('#screen')).filter})");
      if (filters.page !== 'brightness(0.6)' || filters.screen !== 'none') throw new Error(JSON.stringify(filters));
      await evalJs(cdp, "(()=>{const e=document.querySelector('#bright');e.value='100';e.dispatchEvent(new Event('input',{bubbles:true}))})()");
      await apiPost({ action: 'battery', level: 42 }); await reloadAndWait(cdp);
    });
await check('goal note and compact screen', async () => {
    await apiPost({ action: 'battery', level: 5 });
    await apiPost({ action: 'goal', time: '23:00' });
    await reloadAndWait(cdp);
    await waitTone(cdp);
    await evalJs(cdp, "document.querySelector('#meter').click()");
    await new Promise(r => setTimeout(r, 450));
    const { text, display, screenH, screenText } = await evalJs(cdp, `(() => {
        const note = document.querySelector('#goal-note');
        const screen = document.querySelector('#screen');
        return {
            text: note ? note.textContent : '',
            display: note ? getComputedStyle(note).display : '',
            screenH: screen ? screen.offsetHeight : 0,
            screenText: screen ? screen.textContent : ''
        };
    })()`);
    if (text) {
        if (!/^Won't last until 11 PM, calls and messages until ~\d{1,2}:\d{2} (AM|PM)$/.test(text) || display === 'none') {
            throw new Error('goal note mismatch');
        }
    } else {
        if (display !== 'none') {
            throw new Error('goal note should be hidden');
        }
    }
    if (screenH > 220) {
        throw new Error('screen too tall');
    }
    if (!screenText && screenH >= 120) {
        throw new Error('compact screen too tall');
    }
    await apiPost({ action: 'goal', time: '' });
    await apiPost({ action: 'battery', level: 42 });
    await evalJs(cdp, "window.opos.close()");
    await reloadAndWait(cdp);
    await waitTone(cdp);
});
    await check('global timeout diagnostic', async () => {
      const src = fs.readFileSync(fileURLToPath(import.meta.url), 'utf8');
      const m = src.match(/globalTimeout\s*=\s*setTimeout\([\s\S]*?120000/);
      if (!m) throw new Error('globalTimeout not found');
      if (!/console\.error/.test(m[0])) throw new Error('timeout missing diagnostic');
      if (m[0].indexOf('console.error') > m[0].indexOf('cleanup()')) throw new Error('diagnostic after cleanup');
      if (!/currentStage/.test(m[0]) && !/currentTest/.test(m[0])) throw new Error('stage not identified');
    });
    if (shotsDir) {
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 200));
      const closed = await cdp.send('Page.captureScreenshot', { format: 'png' });
      fs.mkdirSync(shotsDir, { recursive: true });
      fs.writeFileSync(path.join(shotsDir, 'closed.png'), Buffer.from(closed.data, 'base64'));
      await evalJs(cdp, "document.querySelector('#meter').click()");
      await new Promise(r => setTimeout(r, 300));
      const open = await cdp.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(shotsDir, 'open.png'), Buffer.from(open.data, 'base64'));
      await apiPost({ action: 'battery', level: 19 });
      await reloadAndWait(cdp);
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 450));
      const closedLow = await cdp.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(shotsDir, 'closed-low.png'), Buffer.from(closedLow.data, 'base64'));
      await apiPost({ action: 'battery', level: 42 });
      await apiPost({ action: 'battery', level: 30 });
      await apiPost({ action: 'goal', time: '23:00' });
      await reloadAndWait(cdp);
      await evalJs(cdp, "document.querySelector('#meter').click()");
      await new Promise(r => setTimeout(r, 450));
      const openSaving = await cdp.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(shotsDir, 'open-saving.png'), Buffer.from(openSaving.data, 'base64'));
      await apiPost({ action: 'call', number: '+15550100' });
      await reloadAndWait(cdp);
      await new Promise(r => setTimeout(r, 300));
      const callShot = await cdp.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(shotsDir, 'call.png'), Buffer.from(callShot.data, 'base64'));
      await apiPost({ action: 'hang_up' });
      await reloadAndWait(cdp);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1000, height: 780, deviceScaleFactor: 1, mobile: false });
      const p = waitForLoadEvent(cdp, 45000); p.catch(() => {}); await cdp.send('Page.navigate', { url: baseUrl + '/?demo=1' }); await p;
      await new Promise(r => setTimeout(r, 450));
      await evalJs(cdp, "window.opos.close()");
      await new Promise(r => setTimeout(r, 200));
      const demoShot = await cdp.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(shotsDir, 'demo.png'), Buffer.from(demoShot.data, 'base64'));
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: 360, height: 780, deviceScaleFactor: 2, mobile: true });
      const q = waitForLoadEvent(cdp, 45000); q.catch(() => {}); await cdp.send('Page.navigate', { url: baseUrl }); await q;
      await apiPost({ action: 'goal', time: '' });
      await apiPost({ action: 'battery', level: 42 });
    }
    results.forEach(r => console.log(r));
    const failed = results.some(r => r.startsWith('FAIL'));
    cdp.close();
    cleanup();
    clearTimeout(globalTimeout);
    process.exit(failed ? 1 : 0);
  } catch (e) {
    console.error(e);
    cleanup();
    clearTimeout(globalTimeout);
    process.exit(1);
  }
})();
