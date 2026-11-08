// SPDX-License-Identifier: GPL-3.0-or-later
// The browser half of tests/test_panes_browser.py: headless Chromium over the DevTools port (node 22+ for the global WebSocket) at
// 1392x900 CSS px, against a real server started with OPOS_PANES=1 and a 675,42 span. It measures; the test file asserts.
// usage: node panes_browser_driver.mjs CHROME BASE_URL DEBUG_PORT PROFILE_DIR OUT_JSON
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import zlib from 'node:zlib';

const [CHROME, BASE, PORT, PROFILE, OUTFILE] = process.argv.slice(2);
const P1 = 675, P2 = 717;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const headless = /headless[_-]shell/.test(CHROME) ? '--headless' : '--headless=new';
const flags = [headless, '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=' + PORT,
  '--user-data-dir=' + PROFILE, ...(process.getuid && process.getuid() === 0 ? ['--no-sandbox'] : []), 'about:blank'];
let proc, ws, seq = 0;
const pending = new Map(), listeners = [];

async function launch() {
  proc = spawn(CHROME, flags, { stdio: 'ignore' });
  let v;
  for (let i = 0; i < 80 && !v; i++) { try { v = await (await fetch('http://127.0.0.1:' + PORT + '/json/version')).json(); } catch (e) { await sleep(250); } }
  if (!v) throw new Error('the browser did not start');
  ws = new WebSocket(v.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('websocket failed')); });
  ws.onmessage = m => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { const p = pending.get(d.id); pending.delete(d.id); d.error ? p.rej(new Error(p.method + ': ' + d.error.message)) : p.res(d.result); }
    else if (d.method) listeners.forEach(l => l(d));
  };
}
function raw(method, params = {}, sessionId) {
  return new Promise((res, rej) => { const id = ++seq; pending.set(id, { res, rej, method });
    ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params })); });
}
function shutdown() {
  try { ws.close(); } catch (e) { /* already closed */ }
  try { proc.kill(); } catch (e) { /* already gone */ }
  if (process.platform === 'win32') { try { spawn('taskkill', ['/T', '/F', '/PID', String(proc.pid)], { stdio: 'ignore' }); } catch (e) { /* gone */ } }
}
async function open(o) {
  const { targetId } = await raw('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await raw('Target.attachToTarget', { targetId, flatten: true });
  const s = (m, p) => raw(m, p, sessionId);
  await s('Page.enable'); await s('Runtime.enable'); await s('Log.enable');
  await s('Emulation.setDeviceMetricsOverride', { width: o.w, height: o.h, deviceScaleFactor: 1, mobile: false });
  await s('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: o.scheme || 'dark' }, { name: 'prefers-reduced-motion', value: o.motion || 'reduce' }] });
  if (o.init) await s('Page.addScriptToEvaluateOnNewDocument', { source: o.init });
  const logs = [];
  listeners.push(d => {
    if (d.sessionId !== sessionId) return;
    if (d.method === 'Runtime.exceptionThrown') logs.push('EXC ' + (d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text));
    if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'error') logs.push('console.error ' + d.params.args.map(a => a.value ?? a.description).join(' '));
    // The server rate-limits /api (HTTP 429) and this run loads many pages in a few seconds: that is the run, not the page.
    if (d.method === 'Log.entryAdded' && d.params.entry.level === 'error' && !/status of 429/.test(d.params.entry.text)) logs.push('log ' + d.params.entry.text + ' ' + (d.params.entry.url || ''));
  });
  await s('Page.navigate', { url: o.url }); await sleep(o.settle || 1400);
  return { s, logs,
    ev: async expr => { const r = await s('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error('page error: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text)); return r.result.value; },
    nav: async url => { await s('Page.navigate', { url }); await sleep(1400); },
    png: async () => Buffer.from((await s('Page.captureScreenshot', { format: 'png' })).data, 'base64'),
    close: () => raw('Target.closeTarget', { targetId }) };
}

function decode(buf) { // 8-bit RGB(A), non-interlaced
  let o = 8, w, h, ct; const idat = [];
  while (o < buf.length) { const n = buf.readUInt32BE(o), t = buf.toString('latin1', o + 4, o + 8), d = buf.subarray(o + 8, o + 8 + n);
    if (t === 'IHDR') { w = d.readUInt32BE(0); h = d.readUInt32BE(4); ct = d[9]; } if (t === 'IDAT') idat.push(d); o += 12 + n; }
  const data = zlib.inflateSync(Buffer.concat(idat)), bpp = ct === 6 ? 4 : 3, stride = w * bpp, out = Buffer.alloc(h * stride);
  for (let y = 0; y < h; y++) { const f = data[y * (stride + 1)], r = data.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) { const a = x >= bpp ? out[y * stride + x - bpp] : 0, b = y ? out[(y - 1) * stride + x] : 0, c = x >= bpp && y ? out[(y - 1) * stride + x - bpp] : 0;
      let v = r[x]; if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1; else if (f === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      out[y * stride + x] = v & 255; } }
  return { w, h, px: (x, y) => { const i = y * stride + x * bpp; return [out[i], out[i + 1], out[i + 2]]; } };
}
const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
const contrast = (a, b) => { const x = lum(a), y = lum(b), h = Math.max(x, y), l = Math.min(x, y); return (h + 0.05) / (l + 0.05); };
const parseRgb = s => /rgba?\(([^)]+)\)/.exec(s)[1].split(',').slice(0, 3).map(Number);
const hex = c => '#' + c.map(v => v.toString(16).padStart(2, '0')).join('');

// What the page looks like right now: text rectangles, controls, colours, effects. Text rectangles come from text nodes of an element
// that owns text; a control is measured by its label when it is a radio or a checkbox.
const MEASURE = `(function(){
 function rgb(s){var m=/rgba?\\(([^)]+)\\)/.exec(s);if(!m)return null;var p=m[1].split(',').map(Number);return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1}}
 var root=document.documentElement,L=rgb(getComputedStyle(root,'::before').backgroundColor),R=rgb(getComputedStyle(root).backgroundColor);
 function backdrop(e,cx){for(var n=e;n&&n.nodeType===1;n=n.parentElement){var b=rgb(getComputedStyle(n).backgroundColor);if(b&&b.a>0.99)return b}return cx<${P2}?L:R}
 function vis(e){var r=e.getBoundingClientRect(),cs=getComputedStyle(e);return r.width>0&&r.height>0&&cs.visibility!=='hidden'&&cs.display!=='none'&&e.getClientRects().length>0}
 var res={hinge:[],small:[],colors:{},bad:[],n:0,rects:[],off:[]};
 var OK={'rgb(0,0,0)':1,'rgb(230,226,217)':1,'rgb(243,240,232)':1,'rgb(26,26,26)':1,'transparent':1};
 function note(c,who,prop){if(!c)return;var k=c.a<0.01?'transparent':'rgb('+c.r+','+c.g+','+c.b+')';res.colors[k]=(res.colors[k]||0)+1;if(!OK[k])res.off.push(k+' '+prop+' '+who)}
 var all=document.querySelectorAll('body *, html');
 for(var i=0;i<all.length;i++){var e=all[i];if(e.closest('#demo')&&!e.closest('#demo[data-demo]'))continue;
  if(e!==root&&!vis(e))continue;var cs=getComputedStyle(e);if(e!==root&&parseFloat(cs.opacity)===0)continue;
  var who=(e.id||e.tagName+'.'+e.className)+(e.type?'['+e.type+']':'');
  ['color','backgroundColor','borderTopColor','borderLeftColor','outlineColor'].forEach(function(k){var w=k==='borderTopColor'?cs.borderTopWidth:k==='borderLeftColor'?cs.borderLeftWidth:k==='outlineColor'?cs.outlineWidth:'1px';if(w==='0px')return;if(k==='backgroundColor'||k==='color'||parseFloat(w)>0)note(rgb(cs[k]),who,k)});
  if(cs.boxShadow!=='none')res.bad.push('shadow '+who);if(cs.filter!=='none')res.bad.push('filter '+who);
  if(cs.backgroundImage!=='none')res.bad.push('bgimage '+who);if(parseFloat(cs.opacity)<1)res.bad.push('opacity '+who);
  if(e===root)continue;
  var rc=e.getBoundingClientRect(),top=document.elementFromPoint(Math.min(innerWidth-1,Math.max(0,rc.left+rc.width/2)),Math.min(innerHeight-1,Math.max(0,rc.top+Math.min(rc.height/2,10))));
  if(top&&!(e.contains(top)||top.contains(e)))continue;
  var own=false;for(var n=e.firstChild;n;n=n.nextSibling){if(n.nodeType===3&&n.textContent.trim())own=true}
  var isCtl=/^(BUTTON|A|INPUT|SELECT|SUMMARY|TEXTAREA)$/.test(e.tagName)&&!(e.tagName==='INPUT'&&/hidden/.test(e.type));
  if(own){var tw=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);while(tw.nextNode()){var t=tw.currentNode;if(t.parentElement!==e||!t.nodeValue.trim())continue;
    var rg=document.createRange();rg.selectNodeContents(t);[].slice.call(rg.getClientRects()).filter(function(r){return r.width>0&&r.height>0}).forEach(function(r){
     res.n++;res.rects.push({id:e.id||e.tagName,l:r.left,t:r.top,r:r.right,b:r.bottom,fg:cs.color,txt:t.nodeValue.trim().slice(0,20)});
     if(r.right>${P1}+0.5&&r.left<${P2}-0.5)res.hinge.push((e.id||e.tagName)+' '+Math.round(r.left)+'-'+Math.round(r.right)+' "'+t.nodeValue.trim().slice(0,20)+'"')})}}
  if(isCtl&&e.id!=='meter'){var tg=/^(radio|checkbox)$/.test(e.type)&&e.closest('label')||e,r=tg.getBoundingClientRect();
   if(r.width<47.5||r.height<47.5)res.small.push(who+' '+Math.round(r.width)+'x'+Math.round(r.height)+' "'+(e.textContent||e.value||'').trim().slice(0,16)+'"');
   if(r.right>${P1}+0.5&&r.left<${P2}-0.5)res.hinge.push('CONTROL '+who+' '+Math.round(r.left)+'-'+Math.round(r.right))}
 }
 var ph=document.getElementById('phone');
 res.attrs={ff:root.dataset.ff||null,panes:root.dataset.panes||null,hand:root.dataset.hand||null,theme:root.dataset.theme||null,app:ph?ph.dataset.app||null:null};
 res.surface={left:getComputedStyle(root,'::before').backgroundColor,right:getComputedStyle(root).backgroundColor,hinge:getComputedStyle(root,'::after').backgroundColor};
 res.cols=(function(){var m=document.querySelector('main,.cols');return m?getComputedStyle(m).gridTemplateColumns:null})();
 function B(id){var e=document.getElementById(id);if(!e)return null;var r=e.getBoundingClientRect();return r.width>0?{l:Math.round(r.left),r:Math.round(r.right),w:Math.round(r.width)}:null}
 res.at={masthead:B('jaryo-masthead'),home:B('home'),back:B('back'),settings:B('settings'),threads:B('threads'),thread:B('thread'),number:B('number'),msg:B('msg'),helper:B('helper'),ask:B('ask'),
  sheet:B('sheet'),contacts:B('contacts'),appsList:B('apps-list'),appsClose:B('apps-close'),unlock:B('unlock'),lockTime:B('lock-time'),notice:B('notice'),
  status:[].map.call(document.querySelectorAll('#jaryo-status-row button'),function(b){var r=b.getBoundingClientRect();return [Math.round(r.left),Math.round(r.right)]})};
 return res})()`;

async function measure(p, extra) {
  const m = await p.ev(MEASURE);
  const img = decode(await p.png());
  m.low = [];
  for (const r of m.rects) {
    if (r.r < 0 || r.l > img.w || r.b < 0 || r.t > img.h) continue;
    const pts = [[r.l + 1, r.t + 1], [r.r - 2, r.t + 1], [r.l + 1, r.b - 2], [r.r - 2, r.b - 2]].map(([x, y]) => img.px(Math.max(0, Math.min(img.w - 1, Math.round(x))), Math.max(0, Math.min(img.h - 1, Math.round(y)))));
    const cnt = {}; pts.forEach(q => { const k = q.join(','); cnt[k] = (cnt[k] || 0) + 1; });
    const bg = Object.entries(cnt).sort((a, b) => b[1] - a[1])[0][0].split(',').map(Number);
    r.cr = contrast(parseRgb(r.fg), bg);
    if (r.cr < 15) m.low.push(r.id + ' ' + r.cr.toFixed(2) + ' ' + r.fg + ' on ' + hex(bg) + ' "' + r.txt + '"');
  }
  m.minCr = m.rects.length ? Math.min(...m.rects.map(r => r.cr ?? 99)) : 99;
  m.pixels = { left: hex(img.px(300, 880)), right: hex(img.px(1000, 880)), hingeTop: hex(img.px(696, 14)), hingeMid: hex(img.px(696, 450)), hingeBottom: hex(img.px(696, 880)) };
  delete m.rects;
  return Object.assign(m, extra || {});
}

const out = { states: {}, hand: {}, off: {}, os: {}, osk: {}, motion: {}, logs: {} };
const MAIN = BASE + '/?firstrun=later';
const setStore = (hand, scheme) => `(function(){try{localStorage.setItem('jaryo_theme','${scheme}');${hand ? `localStorage.setItem('jaryo_hand','${hand}')` : "localStorage.removeItem('jaryo_hand')"}}catch(e){}})()`;

try {
  await launch();
  for (const scheme of ['light', 'dark']) for (const hand of ['right', 'left']) {
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme });
    await p.ev(setStore(hand, scheme)); await p.s('Page.reload'); await sleep(1500);
    const st = async (name, fn) => { if (fn) await p.ev(fn); await sleep(500); out.states[`${scheme}/${hand}/${name}`] = await measure(p); };
    await st('lock-start');
    await st('home', `oposUI.unlock&&oposUI.unlock();oposUI.openApp('home')`);
    await st('allapps', `oposUI.openApp('home');document.getElementById('allapps').click()`);
    await st('phone', `oposUI.openApp('phone')`);
    await st('messages', `oposUI.openApp('messages')`);
    await st('helper', `oposUI.openApp('helper')`);
    await st('settings', `oposUI.openApp('settings')`);
    await st('shade', `oposUI.openApp('home');window.opos.open()`);
    await p.ev(`window.opos.close()`); await sleep(400);
    await st('lock', `oposUI.lock()`);
    out.logs[`${scheme}/${hand}`] = p.logs;
    await p.close();
  }

  { // Hand: the button, a tap, a reload, Enter
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme: 'dark', init: "try{if(!sessionStorage.getItem('fresh')){sessionStorage.setItem('fresh','1');localStorage.removeItem('jaryo_hand')}}catch(e){}" });
    const Q = `(function(){var r=document.documentElement,b=document.getElementById('jaryo-hand'),s=document.getElementById('settings').getBoundingClientRect(),n=document.getElementById('jaryo-masthead').getBoundingClientRect();
      return {hand:r.dataset.hand,stored:localStorage.getItem('jaryo_hand'),label:b&&b.textContent,btnW:b&&Math.round(b.getBoundingClientRect().width),btnH:b&&Math.round(b.getBoundingClientRect().height),prev:b&&b.previousElementSibling&&b.previousElementSibling.id,settingsL:Math.round(s.left),mastheadL:Math.round(n.left),theme:(document.getElementById('jaryo-theme')||{}).textContent,themeAfter:getComputedStyle(document.getElementById('jaryo-theme'),'::after').content}})()`;
    await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('settings')`); await sleep(500);
    out.hand.start = await p.ev(Q);
    await p.ev(`document.getElementById('jaryo-hand').click()`); await sleep(300);
    out.hand.tap = await p.ev(Q);
    await p.s('Page.reload'); await sleep(1500); await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('settings')`); await sleep(500);
    out.hand.reload = await p.ev(Q);
    await p.ev(`document.getElementById('jaryo-hand').click()`); await sleep(300);
    out.hand.tapAgain = await p.ev(Q);
    await p.ev(`document.getElementById('jaryo-hand').focus()`);
    await p.s('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' });
    await p.s('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 }); await sleep(300);
    out.hand.enter = await p.ev(Q);
    out.logs.hand = p.logs;
    await p.close();
  }

  // Off cases: one pane, below the gate, forced Slab, forced Foldable on one pane. No sheet, no Hand button.
  const OFFQ = `(function(){var r=document.documentElement,h=document.getElementById('jaryo-hand'),t=document.getElementById('jaryo-theme');
    return {w:innerWidth,ff:r.dataset.ff||null,panes:r.dataset.panes||null,before:getComputedStyle(r,'::before').content,after:getComputedStyle(r,'::after').content,
     handDisplay:h?getComputedStyle(h).display:'(none)',handRect:h?Math.round(h.getBoundingClientRect().width):0,themeAfter:t?getComputedStyle(t,'::after').content:null,
     htmlBg:getComputedStyle(r).backgroundColor,sheetW:Math.round(document.getElementById('sheet').getBoundingClientRect().width)}})()`;
  for (const [key, w, h, ff] of [['one_pane', 675, 900, null], ['below_gate', 1000, 700, null], ['slab', 1392, 900, 'slab'], ['foldable_675', 675, 900, 'foldable']]) {
    const p = await open({ url: MAIN, w, h, scheme: 'dark', init: `try{localStorage.setItem('jaryo_hand','left');${ff ? `localStorage.setItem('opos_ff','${ff}')` : "localStorage.removeItem('opos_ff')"}}catch(e){}` });
    await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('settings')`); await sleep(600);
    out.off[key] = await p.ev(OFFQ);
    await p.close();
  }

  // The three OS pages, in both hands.
  for (const hand of ['right', 'left']) for (const scheme of ['light', 'dark']) {
    const p = await open({ url: BASE + '/network', w: 1392, h: 900, scheme });
    await p.ev(setStore(hand, scheme)); await p.nav(BASE + '/network');
    const st = async (name, fn) => { if (fn) await p.ev(fn); await sleep(400); out.os[`${scheme}/${hand}/${name}`] = await measure(p); };
    await st('net-idle');
    await st('net-select', `(function(){var s=document.getElementById('sel');s.hidden=false;document.getElementById('sel-t').textContent='Cafe wifi';document.getElementById('prow').hidden=false;document.getElementById('srow').hidden=false;var m=document.getElementById('msg');m.textContent='Could not join. Check the password.';m.toggleAttribute('data-bad',true);var u=document.getElementById('nets');u.textContent='';['Home 5G - strong','Cafe wifi - open'].forEach(function(t){var l=document.createElement('li'),b=document.createElement('button');b.type='button';b.textContent=t;l.appendChild(b);u.appendChild(l)})})()`);
    await p.nav(BASE + '/firstrun'); await st('first-welcome');
    await st('first-zone', `document.getElementById('next').click()`);
    await p.nav(BASE + '/timezone'); await st('timezone');
    out.logs[`os/${scheme}/${hand}`] = p.logs;
    await p.close();
  }

  // The keyboard's halves take the surface they sit on.
  for (const hand of ['right', 'left']) {
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme: 'dark' });
    await p.ev(setStore(hand, 'dark')); await p.s('Page.reload'); await sleep(1800);
    await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('messages')`); await sleep(500);
    await p.s('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
    const c = await p.ev(`(function(){var r=document.getElementById('msg').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()`);
    await p.s('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: c[0], y: c[1] }] }); await sleep(60);
    await p.s('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await sleep(1500);
    out.osk[hand] = await p.ev(`(function(){var k=document.getElementById('opos-osk');if(!k)return null;return {hidden:k.hidden,panels:[].slice.call(k.querySelectorAll('.osk-panel')).map(function(e){var r=e.getBoundingClientRect(),key=e.querySelector('.osk-key');return {l:Math.round(r.left),r:Math.round(r.right),keyBg:getComputedStyle(key).backgroundColor,keyFg:getComputedStyle(key).color}})}})()`);
    out.logs['osk/' + hand] = p.logs;
    await p.close();
  }

  // Motion: with animation on, opening and closing the four apps from Home ends, raises no error and puts the app on the thumb side.
  for (const hand of ['right', 'left']) {
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme: 'dark', motion: 'no-preference' });
    await p.ev(setStore(hand, 'dark') + `;try{localStorage.removeItem('jaryo_motion')}catch(e){}`); await p.s('Page.reload'); await sleep(1600);
    await p.ev(`oposUI.unlock&&oposUI.unlock()`); await sleep(500);
    const res = [];
    for (const app of ['messages', 'phone', 'helper', 'settings']) {
      await p.ev(`document.querySelector('#home [data-open=${app}]').click()`); await sleep(900);
      const st = await p.ev(`(function(){var u=document.getElementById('back').getBoundingClientRect();return {app:document.getElementById('phone').dataset.app,backL:Math.round(u.left),backR:Math.round(u.right),anims:document.getAnimations().length}})()`);
      await p.ev(`document.getElementById('back').click()`); await sleep(900);
      const home = await p.ev(`(function(){var h=document.getElementById('home').getBoundingClientRect();return {app:document.getElementById('phone').dataset.app,homeL:Math.round(h.left),homeR:Math.round(h.right),anims:document.getAnimations().length,focus:document.activeElement&&document.activeElement.dataset&&document.activeElement.dataset.open||null}})()`);
      res.push({ app, st, home });
    }
    out.motion[hand] = res;
    out.logs['motion/' + hand] = p.logs;
    await p.close();
  }
} finally {
  shutdown();
  await sleep(1500);  // let the browser release its profile before the test removes it
}
fs.writeFileSync(OUTFILE, JSON.stringify(out));
process.exit(0);
