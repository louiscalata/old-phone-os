// SPDX-License-Identifier: GPL-3.0-or-later
// The browser half of tests/test_panes_browser.py: headless Chromium over the DevTools port (node 22+ for the global WebSocket) at
// 1392x900 CSS px, against a real server started with OPOS_PANES=1 and a 675,42 span. It measures; the test file asserts.
// usage: node panes_browser_driver.mjs CHROME BASE_URL DEBUG_PORT PROFILE_DIR OUT_JSON [SECTIONS]
// SECTIONS is a comma list of the parts to run (states, hand, off, os, checkbox, osk, motion, ready); without it every part but `ready` runs.
// After a load it waits for the page (tests/panes_wait.mjs), never for a fixed time.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import zlib from 'node:zlib';
import { APP_READY, DOC_READY, sleep, waitFor } from './panes_wait.mjs';

const [CHROME, BASE, PORT, PROFILE, OUTFILE, ONLY] = process.argv.slice(2);
const want = k => (ONLY ? ONLY.split(',').includes(k) : k !== 'ready');
const P1 = 675, P2 = 717;
const headless = /headless[_-]shell/.test(CHROME) ? '--headless' : '--headless=new';
const flags = [headless, '--use-mock-keychain', '--password-store=basic', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=' + PORT,
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
  const ev = async expr => { const r = await s('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('page error: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text)); return r.result.value; };
  // Run `action` (a navigation or a reload), then wait until the page it loads says `cond`. The old document is marked first, so the
  // page being replaced is never taken for the new one; a check that fails while the documents change just polls again.
  const go = async (action, cond) => {
    await ev('window.__opos_old=1').catch(() => {});
    await action();
    await waitFor(() => ev(`typeof window.__opos_old==='undefined'&&(${cond})`), { what: cond });
    await sleep(o.settle ?? 300);  // a moment for the first paint; the page itself was waited for above
  };
  const ready = o.ready || DOC_READY;
  await go(() => s('Page.navigate', { url: o.url }), ready);
  return { s, logs, ev,
    reload: () => go(() => s('Page.reload'), ready),
    nav: (url, cond) => go(() => s('Page.navigate', { url }), cond || ready),
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

// Measure the actual Home buttons, including their fractional bounds, independently of the outer padded grid.
const HOME_TILES = `(function(){return [].slice.call(document.querySelectorAll('#home button')).filter(function(e){
 var r=e.getBoundingClientRect(),cs=getComputedStyle(e);return r.width>0&&r.height>0&&cs.visibility!=='hidden'&&cs.display!=='none'&&e.getClientRects().length>0
 }).map(function(e){var r=e.getBoundingClientRect();return {id:e.id||e.dataset.open,l:r.left,r:r.right,w:r.width,h:r.height}})})()`;

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
 res.root={color:getComputedStyle(root).color,caret:getComputedStyle(root).caretColor};
 res.surface={left:getComputedStyle(root,'::before').backgroundColor,right:getComputedStyle(root).backgroundColor,hinge:getComputedStyle(root,'::after').backgroundColor};
 res.cols=(function(){var m=document.querySelector('main,.cols');return m?getComputedStyle(m).gridTemplateColumns:null})();
 function B(id){var e=document.getElementById(id);if(!e)return null;var r=e.getBoundingClientRect();return r.width>0?{l:Math.round(r.left),r:Math.round(r.right),w:Math.round(r.width)}:null}
 res.at={masthead:B('jaryo-masthead'),home:B('home'),homeTiles:${HOME_TILES},back:B('back'),settings:B('settings'),threads:B('threads'),thread:B('thread'),number:B('number'),msg:B('msg'),helper:B('helper'),ask:B('ask'),
  sheet:B('sheet'),contacts:B('contacts'),appsList:B('apps-list'),appsClose:B('apps-close'),unlock:B('unlock'),lockTime:B('lock-time'),notice:B('notice'),reload:B('reload-shell'),
  status:[].map.call(document.querySelectorAll('#jaryo-status-row button'),function(b){var r=b.getBoundingClientRect();return [Math.round(r.left),Math.round(r.right)]})};
 res.noticeCss=(function(){var e=document.getElementById('notice');if(!e)return null;var cs=getComputedStyle(e);return {bg:cs.backgroundColor,fg:cs.color,border:cs.borderTopStyle+' '+cs.borderTopWidth+' '+cs.borderTopColor,events:cs.pointerEvents,position:cs.position}})();
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

// The pressed state. docs/jaryo-panes.md section 3.3: a pressed control is the inverted fill of its own pane, so its label (the pane's
// background) must read on its fill (the pane's ink). Every visible control is measured twice: with :active forced by the DevTools
// protocol (whatever sits on top of it), and, on request, with a real mouse-down on it (only where nothing covers it), which also
// takes a picture of the button and asks whether any text is left in it.
const CONTROLS = 'button,a.btn,[role=button],.osk-key';
const TAG = `(function(){var n=0;[].forEach.call(document.querySelectorAll('${CONTROLS}'),function(e){e.removeAttribute('data-pt');
 if(e.closest('#demo')&&!e.closest('#demo[data-demo]'))return;
 var r=e.getBoundingClientRect(),cs=getComputedStyle(e);if(r.width<=0||r.height<=0||cs.visibility==='hidden'||cs.display==='none')return;
 e.setAttribute('data-pt',String(n++))});return n})()`;
const UNTAG = `[].forEach.call(document.querySelectorAll('[data-pt]'),function(e){e.removeAttribute('data-pt')})`;
// One entry per tagged control (or the one named): its label colour and the fill under it, and whether anything covers its middle.
const readPressed = only => `(function(){
 function rgb(s){var m=/rgba?\\(([^)]+)\\)/.exec(s);if(!m)return null;var p=m[1].split(',').map(Number);return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1}}
 function lum(c){function f(v){v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)}return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b)}
 function cr(a,b){var x=lum(a),y=lum(b),h=Math.max(x,y),l=Math.min(x,y);return (h+0.05)/(l+0.05)}
 function str(c){return 'rgb('+c.r+','+c.g+','+c.b+')'}
 var root=document.documentElement,L=rgb(getComputedStyle(root,'::before').backgroundColor),R=rgb(getComputedStyle(root).backgroundColor);
 if(!L||L.a<0.99)L=R;
 function fillOf(n,cx){for(;n&&n!==root&&n.nodeType===1;n=n.parentElement){var b=rgb(getComputedStyle(n).backgroundColor);if(b&&b.a>0.99)return b}return cx<${(P1 + P2) / 2}?L:R}
 var out=[];
 [].forEach.call(document.querySelectorAll(${JSON.stringify(only == null ? '[data-pt]' : `[data-pt="${only}"]`)}),function(e){
  var r=e.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2,min=null,fg=null,bg=null,seen=[],tw=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);
  while(tw.nextNode()){var t=tw.currentNode,q=t.parentElement;if(!t.nodeValue.trim()||!q||seen.indexOf(q)>=0)continue;seen.push(q);
   var f=rgb(getComputedStyle(q).color),g=fillOf(q,cx),c=cr(f,g);if(min===null||c<min){min=c;fg=str(f);bg=str(g)}}
  var hit=cx>=0&&cy>=0&&cx<innerWidth&&cy<innerHeight?document.elementFromPoint(cx,cy):null;
  out.push({pt:+e.getAttribute('data-pt'),who:e.id||(e.dataset.open&&'open:'+e.dataset.open)||(e.dataset.go&&'go:'+e.dataset.go)||(e.getAttribute('aria-label')||e.className||e.tagName).toString().slice(0,24),
   txt:(e.textContent||'').trim().slice(0,24),dis:!!e.disabled,ap:e.getAttribute('aria-pressed'),fg:fg,bg:bg,cr:min,fill:str(rgb(getComputedStyle(e).backgroundColor)||{r:0,g:0,b:0,a:0}),active:e.matches(':active'),
   x:r.left,y:r.top,w:r.width,h:r.height,cx:cx,cy:cy,hit:!!hit&&(e===hit||e.contains(hit))})});
 return out})()`;

// A state change made from outside the page (a forced :active, a mouse-down) is not in a computed style until the page has drawn a frame.
const frame = p => p.ev('new Promise(function(r){requestAnimationFrame(function(){requestAnimationFrame(function(){r(1)})})})');

// Forcing :active needs the nodes' DevTools ids, and the page may rebuild a list between asking for them and using them (its own
// timers do): then the whole pass is run again.
async function pressedForced(p) {
  for (let attempt = 0; ; attempt++) {
    try { return await pressedForcedOnce(p); }
    catch (e) { if (attempt >= 4 || !/Could not find node|re-rendered/.test(e.message)) throw e; await p.ev(UNTAG); await sleep(200); }
  }
}
async function pressedForcedOnce(p) {
  if (!(await p.ev(TAG))) return [];
  const rest = await p.ev(readPressed());
  await p.s('DOM.enable'); await p.s('CSS.enable');
  const out = [];
  try {
    const { root } = await p.s('DOM.getDocument', { depth: 0 });
    for (const r of rest) {  // one control at a time: forcing many at once left some of them unstyled when read
      const { nodeId } = await p.s('DOM.querySelector', { nodeId: root.nodeId, selector: `[data-pt="${r.pt}"]` });
      if (!nodeId) throw new Error('the page was re-rendered under the pass');
      try {
        let d;
        for (let tries = 0; tries < 3; tries++) {  // the browser sometimes restyles late: a control that did not change is forced again
          await p.s('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: ['active'] });
          await frame(p);
          [d] = await p.ev(readPressed(r.pt));
          if (!d || d.bg !== r.bg || d.dis || d.ap === 'true' || !d.txt) break;  // (the others are not expected to change)
          await p.s('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: [] });
        }
        if (!d) throw new Error('the page was re-rendered under the pass');
        out.push({ who: d.who, txt: d.txt, dis: d.dis, ap: d.ap, fg: d.fg, bg: d.bg, cr: d.cr, rfg: r.fg, rbg: r.bg, active: d.active, inv: d.bg !== r.bg });
      } finally { await p.s('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: [] }).catch(() => {}); }
    }
    return out;
  } finally {
    await p.s('CSS.disable').catch(() => {}); await p.s('DOM.disable').catch(() => {});
    await p.ev(UNTAG);
  }
}

// The pixels of a button: the contrast between its lightest and darkest pixel inside the border. With the label gone (ink on ink) that
// is 1; with a label it is the contrast of the two inks.
function inkContrast(png) {
  const img = decode(png);
  let lo = 2, hi = -1;
  for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) { const l = lum(img.px(x, y)); if (l < lo) lo = l; if (l > hi) hi = l; }
  return (hi + 0.05) / (lo + 0.05);
}

async function pressedReal(p, name) {
  if (!(await p.ev(TAG))) return [];
  const list = await p.ev(readPressed());
  const done = [];
  // A press is not a click: the page must stay where it is, so clicks are stopped before they reach the page.
  await p.ev(`window.__pb=function(e){e.preventDefault();e.stopImmediatePropagation()};addEventListener('click',window.__pb,true)`);
  try {
    for (const c of list) {
      if (!c.hit || c.w < 12 || c.h < 12) continue;
      await p.s('Input.dispatchMouseEvent', { type: 'mouseMoved', x: c.cx, y: c.cy });
      await p.s('Input.dispatchMouseEvent', { type: 'mousePressed', x: c.cx, y: c.cy, button: 'left', buttons: 1, clickCount: 1 });
      let d;
      for (let tries = 0; tries < 4; tries++) {  // as above: a few frames for the press to show in the style
        await frame(p);
        [d] = await p.ev(readPressed(c.pt));
        if (!d || d.bg !== c.bg || d.dis || d.ap === 'true' || !d.txt) break;
        await sleep(60);
      }
      if (!d) { await p.s('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.cx, y: c.cy, button: 'left', buttons: 0, clickCount: 1 }); continue; }  // the page rebuilt it: not measured
      const inset = 4, clip = { x: Math.floor(c.x) + inset, y: Math.floor(c.y) + inset, width: Math.floor(c.w) - 2 * inset, height: Math.floor(c.h) - 2 * inset, scale: 1 };
      const shot = Buffer.from((await p.s('Page.captureScreenshot', { format: 'png', clip })).data, 'base64');
      let whole = null, zoom = null;  // pictures while the button is still down
      if (SHOTS && name && SHOT_IDS.has(c.who)) {
        whole = await p.png();
        const z = { x: Math.max(0, Math.floor(c.x) - 24), y: Math.max(0, Math.floor(c.y) - 24), width: Math.floor(c.w) + 48, height: Math.floor(c.h) + 48, scale: 2 };
        zoom = Buffer.from((await p.s('Page.captureScreenshot', { format: 'png', clip: z })).data, 'base64');
      }
      await p.s('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.cx, y: c.cy, button: 'left', buttons: 0, clickCount: 1 });
      if (whole) { save(`pressed-${name}-${c.who}.png`, whole); save(`pressed-${name}-${c.who}-zoom.png`, zoom); }
      done.push({ who: d.who, txt: d.txt, dis: d.dis, ap: d.ap, fg: d.fg, bg: d.bg, cr: d.cr, rbg: c.bg, active: d.active, inv: d.bg !== c.bg, pix: inkContrast(shot) });
    }
  } finally {
    await p.s('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 1, y: 1 });
    await p.ev(`removeEventListener('click',window.__pb,true);delete window.__pb;` + UNTAG);
  }
  return done;
}

// PANES_SHOTS=dir: also save pictures of what the run looked at (here the four primary buttons while pressed).
const SHOTS = process.env.PANES_SHOTS;
const SHOT_IDS = new Set(['back', 'apps-close', 'unlock', 'lock-call']), SHOT_STATES = new Set(['phone', 'allapps', 'lock-start']);
const save = (name, buf) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); fs.writeFileSync(SHOTS + '/' + name, buf); } };

const out = { states: {}, hand: {}, off: {}, os: {}, osk: {}, motion: {}, logs: {} };
// The low-battery chip: its word, its box, its size, and the Shade's title.
const LOW = `(function(){var b=document.getElementById('sb-batt'),c=getComputedStyle(b),r=b.getBoundingClientRect(),s=document.getElementById('sb').getBoundingClientRect();
 return {text:b.textContent,low:b.getAttribute('data-low'),border:c.borderTopWidth+' '+c.borderTopStyle,borderColor:c.borderTopColor,color:c.color,h:Math.round(r.height*10)/10,l:Math.round(r.left),r:Math.round(r.right),
  inBar:r.top>=s.top&&r.bottom<=s.bottom,title:document.getElementById('sheet-title').textContent}})()`;
// What a call in progress looks like: the notice (boxed, bold, in the pane's ink), Hang up (inverted fill), the status bar's word.
const CALL = `(function(){function B(e){var r=e.getBoundingClientRect();return {l:Math.round(r.left),r:Math.round(r.right),h:Math.round(r.height)}}
 var n=document.getElementById('notice'),c=getComputedStyle(n),h=document.querySelector('#controls [data-go=hang_up]'),k=getComputedStyle(h),g=document.getElementById('sb-net');
 return {tone:n.dataset.tone,hidden:n.hidden,text:n.textContent,fontSize:c.fontSize,fontWeight:c.fontWeight,border:c.borderTopWidth+' '+c.borderTopStyle,borderColor:c.borderTopColor,color:c.color,box:B(n),
  hang:{fg:k.color,bg:k.backgroundColor,box:B(h)},net:g.textContent,screen:document.getElementById('screen').textContent}})()`;
const MAIN = BASE + '/?firstrun=later';
// What each page needs before it is measured: the kiosk page can open an app; the Network page has read its radios and scanned; the
// first-run page has its choices and the time zone page its list (both come from the server after the page has loaded).
const NET_READY = DOC_READY + "&&document.getElementById('st').textContent.indexOf('Reading the radios')<0&&!!(document.querySelector('#nets li')||document.getElementById('msg').textContent)";
const FIRST_READY = DOC_READY + "&&!!document.getElementById('next')&&!document.getElementById('next').disabled";
const ZONE_READY = DOC_READY + "&&!!document.querySelector('#tzbox .list button')";
const setStore = (hand, scheme) => `(function(){try{localStorage.setItem('jaryo_theme','${scheme}');${hand ? `localStorage.setItem('jaryo_hand','${hand}')` : "localStorage.removeItem('jaryo_hand')"}}catch(e){}})()`;

// The Network page's "Show the password" checkbox, as the page draws it: what the style says, and the pixels of the box itself.
const SHOW_PASSWORD = `(function(){['sel','prow','srow'].forEach(function(i){document.getElementById(i).hidden=false})})()`;
async function checkboxProbe(p, name) {
  const g = await p.ev(`(function(){var e=document.getElementById('show');e.scrollIntoView({block:'center'});var r=e.getBoundingClientRect(),cs=getComputedStyle(e),row=e.closest('label').getBoundingClientRect();
    return {x:r.left+scrollX,y:r.top+scrollY,w:r.width,h:r.height,rowH:Math.round(row.height),checked:e.checked,appearance:cs.appearance,width:cs.width,height:cs.height,
     border:cs.borderTopWidth+' '+cs.borderTopStyle+' '+cs.borderTopColor,radius:cs.borderTopLeftRadius,accent:cs.accentColor,color:cs.color,fill:cs.backgroundColor}})()`);
  const png = Buffer.from((await p.s('Page.captureScreenshot', { format: 'png', clip: { x: Math.floor(g.x), y: Math.floor(g.y), width: Math.ceil(g.w), height: Math.ceil(g.h), scale: 1 } })).data, 'base64');
  const img = decode(png), colours = {};
  for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) { const k = hex(img.px(x, y)); colours[k] = (colours[k] || 0) + 1; }
  g.colours = colours; g.centre = hex(img.px(img.w >> 1, img.h >> 1)); g.size = [img.w, img.h];
  if (SHOTS) {
    save(`checkbox-${name}.png`, await p.png());
    save(`checkbox-${name}-zoom.png`, Buffer.from((await p.s('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, g.x - 24), y: Math.max(0, g.y - 20), width: 360, height: g.h + 40, scale: 3 } })).data, 'base64'));
  }
  return g;
}

try {
  await launch();
  if (want('states')) for (const scheme of ['light', 'dark']) for (const hand of ['right', 'left']) {
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme, ready: APP_READY });
    await p.ev(setStore(hand, scheme)); await p.reload();
    const st = async (name, fn, probe) => { if (fn) await p.ev(fn); await sleep(500);
      const m = await measure(p, probe && { probe: await p.ev(probe) });  // then every control pressed: forced :active everywhere, a real mouse-down in the dark run
      m.press = { forced: await pressedForced(p), real: scheme === 'dark' ? await pressedReal(p, SHOT_STATES.has(name) ? `${hand}-${name}` : null) : [] };
      out.states[`${scheme}/${hand}/${name}`] = m; };
    await st('lock-start');
    await st('home', `oposUI.unlock&&oposUI.unlock();oposUI.openApp('home')`);
    await st('allapps', `oposUI.openApp('home');document.getElementById('allapps').click()`);
    await st('phone', `oposUI.openApp('phone')`);
    // A call in progress (interface plan W1-06): the boxed bold notice and the inverted Hang up, measured like every other state. The
    // call is ended right after, so the states that follow are the idle ones.
    // A call polls once a second and each poll rebuilds the contact buttons, which would invalidate the nodes the pressed pass holds: the
    // page's render is held still while this state is measured, and let go before the call is ended.
    await st('phone-call', `(async()=>{document.getElementById('number').value='+15550100';await go('call');await new Promise(r=>setTimeout(r,300));window.__r=render;render=async function(){}})()`, CALL);
    await p.ev(`(async()=>{render=window.__r;delete window.__r;await go('hang_up')})()`);
    // Low battery (W1-05): "LOW 12%" in a double box in the right pane's ink; the battery is put back after.
    await st('home-low', `(async()=>{oposUI.openApp('home');render(await api({action:'battery',level:12}))})()`, LOW);
    await p.ev(`(async()=>{render(await api({action:'battery',level:60}))})()`);
    await st('messages', `oposUI.openApp('messages')`);
    await st('helper', `oposUI.openApp('helper')`);
    await st('settings', `oposUI.openApp('settings')`);
    // A page whose server went away: two lost polls (and every request lost from here on), the banner and the Reload button.
    await st('stale', `oposUI.openApp('home');window.__fetch=window.fetch;window.fetch=function(){return Promise.reject(new TypeError('down'))};oposPoll(0);oposPoll(0)`);
    await p.ev(`window.fetch=window.__fetch;oposPoll(1);fetchState().then(render)`); await sleep(800);
    await st('shade', `oposUI.openApp('home');window.opos.open()`);
    await p.ev(`window.opos.close()`); await sleep(400);
    await st('lock', `oposUI.lock()`);
    out.logs[`${scheme}/${hand}`] = p.logs;
    await p.close();
  }

  if (want('hand')) { // Hand: the button, a tap, a reload, Enter
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme: 'dark', ready: APP_READY, init: "try{if(!sessionStorage.getItem('fresh')){sessionStorage.setItem('fresh','1');localStorage.removeItem('jaryo_hand')}}catch(e){}" });
    const Q = `(function(){var r=document.documentElement,b=document.getElementById('jaryo-hand'),s=document.getElementById('settings').getBoundingClientRect(),n=document.getElementById('jaryo-masthead').getBoundingClientRect();
      return {hand:r.dataset.hand,stored:localStorage.getItem('jaryo_hand'),label:b&&b.textContent,btnW:b&&Math.round(b.getBoundingClientRect().width),btnH:b&&Math.round(b.getBoundingClientRect().height),prev:b&&b.previousElementSibling&&b.previousElementSibling.id,settingsL:Math.round(s.left),mastheadL:Math.round(n.left),theme:(document.getElementById('jaryo-theme')||{}).textContent,themeAfter:getComputedStyle(document.getElementById('jaryo-theme'),'::after').content}})()`;
    await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('settings')`); await sleep(500);
    out.hand.start = await p.ev(Q);
    await p.ev(`document.getElementById('jaryo-hand').click()`); await sleep(300);
    out.hand.tap = await p.ev(Q);
    await p.reload(); await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('settings')`); await sleep(500);
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
  if (want('off')) for (const [key, w, h, ff] of [['one_pane', 675, 900, null], ['below_gate', 1000, 700, null], ['slab', 1392, 900, 'slab'], ['foldable_675', 675, 900, 'foldable']]) {
    const p = await open({ url: MAIN, w, h, scheme: 'dark', ready: APP_READY, init: `try{localStorage.setItem('jaryo_hand','left');${ff ? `localStorage.setItem('opos_ff','${ff}')` : "localStorage.removeItem('opos_ff')"}}catch(e){}` });
    await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('settings')`); await sleep(600);
    out.off[key] = await p.ev(OFFQ);
    await p.close();
  }

  // The three OS pages, in both hands.
  if (want('os')) for (const hand of ['right', 'left']) for (const scheme of ['light', 'dark']) {
    const p = await open({ url: BASE + '/network', w: 1392, h: 900, scheme, ready: NET_READY });
    await p.ev(setStore(hand, scheme)); await p.nav(BASE + '/network');
    const st = async (name, fn) => { if (fn) await p.ev(fn); await sleep(400);
      const m = await measure(p);
      m.press = { forced: await pressedForced(p), real: scheme === 'dark' ? await pressedReal(p, null) : [] };
      out.os[`${scheme}/${hand}/${name}`] = m; };
    await st('net-idle');
    await st('net-select', `(function(){var s=document.getElementById('sel');s.hidden=false;document.getElementById('sel-t').textContent='Cafe wifi';document.getElementById('prow').hidden=false;document.getElementById('srow').hidden=false;var m=document.getElementById('msg');m.textContent='Could not join. Check the password.';m.toggleAttribute('data-bad',true);var u=document.getElementById('nets');u.textContent='';['Home 5G - strong','Cafe wifi - open'].forEach(function(t){var l=document.createElement('li'),b=document.createElement('button');b.type='button';b.textContent=t;l.appendChild(b);u.appendChild(l)})})()`);
    await p.nav(BASE + '/firstrun', FIRST_READY); await st('first-welcome');
    await st('first-zone', `document.getElementById('next').click()`);
    await p.nav(BASE + '/timezone', ZONE_READY); await st('timezone');
    out.logs[`os/${scheme}/${hand}`] = p.logs;
    await p.close();
  }

  // The password checkbox, unchecked and checked, where the panes do not paint (a phone, a window below the two-panel gate: the page's own
  // rules draw it) and where they do (1392 x 900, both hands).
  if (want('checkbox')) { out.checkbox = {}; for (const [key, w, h, scheme, hand] of [['phone-light', 390, 844, 'light'], ['phone-dark', 390, 844, 'dark'], ['below-light', 1000, 700, 'light'],
      ['below-dark', 1000, 700, 'dark'], ['span-right', 1392, 900, 'dark', 'right'], ['span-left', 1392, 900, 'dark', 'left']]) {
    const p = await open({ url: BASE + '/network', w, h, scheme, ready: NET_READY });
    await p.ev(setStore(hand, scheme)); await p.nav(BASE + '/network');
    await p.ev(SHOW_PASSWORD);
    out.checkbox[key] = {};
    for (const checked of [false, true]) {
      await p.ev(`document.getElementById('show').checked=${checked}`); await frame(p);
      out.checkbox[key][checked ? 'checked' : 'unchecked'] = await checkboxProbe(p, `${key}-${checked ? 'checked' : 'unchecked'}`);
    }
    out.logs['checkbox/' + key] = p.logs;
    await p.close();
  } }

  // The keyboard's halves take the surface they sit on.
  if (want('osk')) for (const hand of ['right', 'left']) {
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme: 'dark', ready: APP_READY });
    await p.ev(setStore(hand, 'dark')); await p.reload();
    await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('messages')`); await sleep(500);
    await p.s('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
    const c = await p.ev(`(function(){var r=document.getElementById('msg').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()`);
    await p.s('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: c[0], y: c[1] }] }); await sleep(60);
    await p.s('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await waitFor(() => p.ev(`(function(){var k=document.getElementById('opos-osk');return !!k&&!k.hidden})()`), { what: 'the keyboard', timeout: 5000 }).catch(() => {});  // none: the result says so
    await sleep(300);
    out.osk[hand] = await p.ev(`(function(){var k=document.getElementById('opos-osk');if(!k)return null;return {hidden:k.hidden,panels:[].slice.call(k.querySelectorAll('.osk-panel')).map(function(e){var r=e.getBoundingClientRect(),key=e.querySelector('.osk-key');return {l:Math.round(r.left),r:Math.round(r.right),keyBg:getComputedStyle(key).backgroundColor,keyFg:getComputedStyle(key).color}})}})()`);
    if (out.osk[hand]) out.osk[hand].press = await pressedForced(p);
    out.logs['osk/' + hand] = p.logs;
    await p.close();
  }

  // Motion: with animation on, opening and closing the four apps from Home ends, raises no error and puts the app on the thumb side.
  if (want('motion')) for (const hand of ['right', 'left']) {
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme: 'dark', motion: 'no-preference', ready: APP_READY });
    await p.ev(setStore(hand, 'dark') + `;try{localStorage.removeItem('jaryo_motion')}catch(e){}`); await p.reload();
    await p.ev(`oposUI.unlock&&oposUI.unlock()`); await sleep(500);
    const res = [];
    for (const app of ['messages', 'phone', 'helper', 'settings']) {
      await p.ev(`document.querySelector('#home [data-open=${app}]').click()`); await sleep(900);
      const st = await p.ev(`(function(){var u=document.getElementById('back').getBoundingClientRect();return {app:document.getElementById('phone').dataset.app,backL:Math.round(u.left),backR:Math.round(u.right),anims:document.getAnimations().length}})()`);
      await p.ev(`document.getElementById('back').click()`); await sleep(900);
      const home = await p.ev(`(function(){var h=document.getElementById('home').getBoundingClientRect();return {app:document.getElementById('phone').dataset.app,homeL:Math.round(h.left),homeR:Math.round(h.right),tiles:${HOME_TILES},anims:document.getAnimations().length,focus:document.activeElement&&document.activeElement.dataset&&document.activeElement.dataset.open||null}})()`);
      res.push({ app, st, home });
    }
    out.motion[hand] = res;
    out.logs['motion/' + hand] = p.logs;
    await p.close();
  }

  // A slow page: the driver waits for the page, not for a fixed time (tests/test_panes_browser.py serves this one slowly on purpose).
  if (want('ready')) {
    const p = await open({ url: MAIN, w: 1392, h: 900, scheme: 'dark', ready: APP_READY });
    await p.ev(setStore('right', 'dark')); await p.reload();
    await p.ev(`oposUI.unlock&&oposUI.unlock();oposUI.openApp('settings')`); await sleep(300);
    out.ready = await p.ev(`(function(){var r=document.documentElement;return {app:document.getElementById('phone').dataset.app,ff:r.dataset.ff||null,panes:r.dataset.panes||null}})()`);
    await p.close();
  }
} finally {
  shutdown();
  await sleep(1500);  // let the browser release its profile before the test removes it
}
fs.writeFileSync(OUTFILE, JSON.stringify(out));
process.exit(0);
