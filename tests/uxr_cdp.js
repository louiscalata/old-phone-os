// SPDX-License-Identifier: GPL-3.0-or-later
// UXP14: drive headless Chromium over the DevTools protocol (Node built-ins only: fetch + WebSocket).
// Usage: node uxr_cdp.js <port|pipe> <file-url> <check> <state.json>
// <port>: a browser started by the test with --remote-debugging-port=0. "pipe": no local port can be bound (sandbox),
// so this script starts $UXR_CHROME itself with --remote-debugging-pipe and writes its PID to $UXR_PIDFILE.
// Prints one JSON object {"violations":[...]} on stdout; each violation is "<viewport> <view> <selector> <measurement>".
// Views: "full" = the page as served, state fetches fail (fail-open shell); "home" = canned /api state served by a
// fetch stub (installed with Page.addScriptToEvaluateOnNewDocument), shell forced to home via oposUI.openApp('home').
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const [port, fileUrl, check, stateFile] = process.argv.slice(2);
const STATE = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
const VIEWPORTS = [[360, 640], [720, 1280], [1280, 720]];
const HELPER_ACTIONS = ['ask', 'plan', 'explain', 'visible'];
const sleep = ms => new Promise(r => setTimeout(r, ms));

let raw, session, seq = 0;
const pending = new Map();
function onMessage(d) {
  const p = d.id && pending.get(d.id);
  if (p) { pending.delete(d.id); d.error ? p.rej(new Error(p.method + ': ' + d.error.message)) : p.res(d.result); }
}
function sendTo(sessionId, method, params = {}) {
  return new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, { res, rej, method });
    raw(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }));
  });
}
const send = (method, params) => sendTo(session, method, params);
async function connect() {
  if (port !== 'pipe') {
    const v = await (await fetch('http://127.0.0.1:' + port + '/json/version')).json();
    const ws = new WebSocket(v.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('websocket failed')); });
    ws.onmessage = m => onMessage(JSON.parse(m.data));
    raw = s => ws.send(s);
    return () => { try { ws.close(); } catch (e) {} };
  }
  const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'uxr-pipe-'));
  const child = spawn(process.env.UXR_CHROME, ['--headless', '--use-mock-keychain', '--password-store=basic', '--no-sandbox', '--disable-gpu', '--single-process',
    '--remote-debugging-pipe', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check', 'about:blank'],
    { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
  if (process.env.UXR_PIDFILE) fs.writeFileSync(process.env.UXR_PIDFILE, String(child.pid));
  let buf = Buffer.alloc(0);
  child.stdio[4].on('data', c => {
    buf = Buffer.concat([buf, c]);
    for (let i; (i = buf.indexOf(0)) >= 0; buf = buf.subarray(i + 1)) onMessage(JSON.parse(buf.subarray(0, i).toString()));
  });
  raw = s => child.stdio[3].write(s + '\0');
  return () => { try { child.kill('SIGKILL'); } catch (e) {} try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) {} };
}
async function ev(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('page error: ' + (r.exceptionDetails.exception && r.exceptionDetails.exception.description || r.exceptionDetails.text));
  return r.result.value;
}
async function waitFor(expr, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await ev('!!(' + expr + ')')) return true; await sleep(25); }
  return false;
}

// Fetch stub: only active when the URL carries uxr=stub. Records every call in window.__uxr.fetches.
const STUB = `(function(){if(!/[?&]uxr=stub/.test(location.search))return;var S=${JSON.stringify(STATE)},vis=false,log=[];
window.__uxr={fetches:log};var real=window.fetch;
function resp(o){return Promise.resolve(new Response(JSON.stringify(o),{status:200,headers:{'Content-Type':'application/json'}}))}
function st(){return JSON.parse(JSON.stringify(vis?S.visible:S.hidden))}
window.fetch=function(u,o){var url=String(u&&u.url||u),b={};try{b=JSON.parse(o&&o.body||'{}')}catch(e){}log.push({url:url,action:b.action||'',number:b.number,text:b.text});
 if(/\\/api\\/state$/.test(url))return resp(st());
 if(/\\/api$/.test(url)){if(b.action==='visible')vis=!!b.visible;
  if(b.action==='ask'||b.action==='plan'||b.action==='explain')return resp({text:'ok',engine:'rules',where:'on this phone',order:[]});
  return resp(st())}
 return real.apply(this,arguments)}})();`;

// Fault injection (uxr=boom): window.oposUI and window.oposHooks throw on use, so phone-a.js and phone-b.js
// (the new shell JS) raise on load and on every render. window.__uxrBoom counts the throws.
const BOOM = `(function(){if(!/[?&]uxr=boom/.test(location.search))return;window.__uxrBoom=0;
function t(){window.__uxrBoom++;throw new Error('uxr boom')}
Object.defineProperty(window,'oposUI',{configurable:true,get:t,set:t});
window.oposHooks={push:t,forEach:t}})();`;

// In-page helpers (window.__u).
const LIB = `window.__u={
vis:function(e){if(e.closest('[hidden]')||e.closest('[inert]'))return false;var c=getComputedStyle(e);if(c.display==='none'||c.visibility!=='visible')return false;var r=e.getBoundingClientRect();return r.width>0&&r.height>0},
sel:function(e){if(e.id)return '#'+e.id;var p=[];while(e&&e.nodeType===1&&!e.id){var s=e.tagName.toLowerCase(),a=e.getAttribute('data-open')||e.getAttribute('data-go');
 if(a)s+='[data-'+(e.hasAttribute('data-open')?'open':'go')+'='+a+']';else if(e.parentNode&&e.parentNode.children){var i=1,x=e;while((x=x.previousElementSibling))if(x.tagName===e.tagName)i++;s+=':nth-of-type('+i+')'}
 p.unshift(s);e=e.parentElement}if(e&&e.id)p.unshift('#'+e.id);return p.join('>')},
inter:function(){return Array.prototype.filter.call(document.querySelectorAll('button,a[href],input,select,summary,[role=button]'),this.vis)},
rgba:function(s){var m=String(s).match(/rgba?\\(([^)]+)\\)/);if(!m)return null;var v=m[1].split(/[\\s,\\/]+/).filter(Boolean).map(parseFloat);return [v[0],v[1],v[2],v.length>3?v[3]:1]},
over:function(f,b){var a=f[3];return [f[0]*a+b[0]*(1-a),f[1]*a+b[1]*(1-a),f[2]*a+b[2]*(1-a),1]},
lum:function(c){var l=c.slice(0,3).map(function(v){v/=255;return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4)});return 0.2126*l[0]+0.7152*l[1]+0.0722*l[2]},
txt:function(e){return (e.innerText||e.value||e.getAttribute('aria-label')||'').replace(/\\s+/g,' ').trim().slice(0,24)}
};true`;

async function load(view, w, h, landscape) {
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: true,
    screenOrientation: landscape ? { type: 'landscapePrimary', angle: 90 } : { type: 'portraitPrimary', angle: 0 } });
  await send('Page.navigate', { url: fileUrl + (view === 'home' ? '?lock=0&uxr=stub' : view === 'boom' ? '?uxr=stub&uxr=boom' : '') });
  await sleep(50);
  if (!await waitFor(`document.readyState==='complete'&&location.protocol==='file:'`)) throw new Error('page did not load');
  if (view === 'home') {
    if (!await waitFor(`document.getElementById('qs')`)) throw new Error('first render never ran');
    await ev(`window.oposUI&&oposUI.openApp?oposUI.openApp('home'):(document.querySelector('#home [data-open]')||{click(){}}).click()`);
    if (!await waitFor(`document.getElementById('phone').dataset.app==='home'`)) throw new Error('home view did not open');
  } else await sleep(150);
  await ev(LIB);
}
async function resize(w, h) {
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: true,
    screenOrientation: w > h ? { type: 'landscapePrimary', angle: 90 } : { type: 'portraitPrimary', angle: 0 } });
  await ev('new Promise(function(r){requestAnimationFrame(function(){requestAnimationFrame(function(){setTimeout(r,80)})})})');
}
async function eachView(views, sizes, fn) {
  const out = [];
  for (const [w, h] of sizes) for (const view of views) {
    await load(view, w, h, w > h);
    for (const v of await fn(view, w, h)) out.push(w + 'x' + h + ' ' + view + ' ' + v);
  }
  return out;
}

const CHECKS = {
  async touch_targets() {
    return eachView(['full', 'home'], VIEWPORTS, view => ev(`(function(){var U=__u,VIEW=${JSON.stringify(view)},A=(window.__uxrAllow||{}).size||{},O=(window.__uxrAllow||{}).overlap||{},out=[],els=U.inter(),R=els.map(function(e){return e.getBoundingClientRect()});
     function floats(e){var v=O[U.sel(e)];return !!v&&v.indexOf(VIEW)>=0}
     els.forEach(function(e,i){var s=U.sel(e),min=s in A?A[s]:48,r=R[i];if(r.width<min||r.height<min)out.push(s+' size '+Math.round(r.width)+'x'+Math.round(r.height)+' < '+min+' "'+U.txt(e)+'"')});
     for(var i=0;i<els.length;i++)for(var j=i+1;j<els.length;j++){if(els[i].contains(els[j])||els[j].contains(els[i]))continue;var a=R[i],b=R[j];
      var g=Math.max(b.left-a.right,a.left-b.right,b.top-a.bottom,a.top-b.bottom);
      if(floats(els[i])||floats(els[j])){if(g>=0)continue;var f=floats(els[i])?a:b,t=f===a?b:a;
       var free=[[t.left,Math.min(t.right,f.left),t.top,t.bottom],[Math.max(t.left,f.right),t.right,t.top,t.bottom],[t.left,t.right,t.top,Math.min(t.bottom,f.top)],[t.left,t.right,Math.max(t.top,f.bottom),t.bottom]];
       if(!free.some(function(q){return q[1]-q[0]>=48&&q[3]-q[2]>=48}))out.push(U.sel(els[i])+' <-> '+U.sel(els[j])+' a floating control leaves no free 48x48 part of the one under it');continue}
      if(g<8)out.push(U.sel(els[i])+' <-> '+U.sel(els[j])+' gap '+Math.round(g)+'px < 8')}
     return out})()`));
  },
  async contrast() {
    return eachView(['full', 'home'], VIEWPORTS, () => ev(`(function(){var U=__u,out=[],seen=new Set(),w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
     while(w.nextNode()){var n=w.currentNode,e=n.parentElement;if(!e||seen.has(e)||!n.nodeValue.trim()||/^(SCRIPT|STYLE|TITLE)$/.test(e.tagName)||!U.vis(e))continue;seen.add(e);
      var c=getComputedStyle(e),fg=U.rgba(c.color);if(!fg)continue;var op=1,st=[];for(var a=e;a;a=a.parentElement){var ac=getComputedStyle(a);op*=parseFloat(ac.opacity);var b=U.rgba(ac.backgroundColor);if(b&&b[3]>0)st.push(b)}
      var bg=[255,255,255,1];while(st.length)bg=U.over(st.pop(),bg);fg=U.over([fg[0],fg[1],fg[2],fg[3]*op],bg);var L1=U.lum(fg),L2=U.lum(bg),ratio=(Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
      var px=parseFloat(c.fontSize),large=px>=24||(px>=18.66&&parseInt(c.fontWeight,10)>=700),need=large?3:4.5;
      if(ratio<need-0.005)out.push(U.sel(e)+' contrast '+ratio.toFixed(2)+' < '+need+' ('+px+'px "'+n.nodeValue.trim().slice(0,24)+'")')}
     return out})()`));
  },
  async text_200() {
    return eachView(['full', 'home'], [[360, 640]], async () => {
      // Record each visible control/label font size first; after the root change it must reach ~2x.
      await ev(`__u.txtEls=function(){return __u.inter().concat(Array.prototype.filter.call(document.querySelectorAll('label'),__u.vis))};
       window.__uxrFs=new Map(__u.txtEls().map(function(e){return [e,parseFloat(getComputedStyle(e).fontSize)]}));
       document.documentElement.style.fontSize='200%';new Promise(function(r){requestAnimationFrame(function(){setTimeout(r,50)})})`);
      return ev(`(function(){var U=__u,out=[],d=document.documentElement;if(d.scrollWidth>innerWidth+1)out.push('html page scrollWidth '+d.scrollWidth+' > innerWidth '+innerWidth);
       __uxrFs.forEach(function(px,e){var now=parseFloat(getComputedStyle(e).fontSize);if(now<px*1.9)out.push(U.sel(e)+' text not doubled '+px+'px -> '+now+'px "'+U.txt(e)+'"')});
       U.txtEls().forEach(function(e){if(/^INPUT$/.test(e.tagName)&&e.type!=='button')return;
        if(e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1&&getComputedStyle(e).overflowY!=='visible')out.push(U.sel(e)+' clipped scroll '+e.scrollWidth+'x'+e.scrollHeight+' > client '+e.clientWidth+'x'+e.clientHeight+' "'+U.txt(e)+'"')});
       return out})()`);
    });
  },
  async rotation_without_reload() {
    const out = [];
    await load('home', 720, 1280, false);
    const snap = `(function(){var s=document.scrollingElement,a=document.activeElement;return {m:window.__opos_marker,
      order:Array.prototype.map.call(document.querySelectorAll('#home button'),function(b){return (b.id||b.dataset.open||'')+'='+b.textContent.trim()}).join('|'),
      active:a&&a!==document.body?__u.sel(a):'body',top:s?s.scrollTop:0}})()`;
    await ev(`window.__opos_marker=1;var b=document.querySelector('#home button');if(b)b.focus();true`);
    const before = await ev(snap);
    if (before.m !== 1 || !before.order) return ['720x1280 home #home no home buttons or marker missing before rotation'];
    for (const [w, h] of [[1280, 720], [720, 1280]]) {
      await resize(w, h);
      const now = await ev(snap);
      const at = w + 'x' + h + ' home ';
      if (now.m !== 1) out.push(at + 'window marker lost (page reloaded)');
      if (now.order !== before.order) out.push(at + '#home order ' + now.order + ' != ' + before.order);
      if (now.active !== before.active) out.push(at + 'document.activeElement ' + now.active + ' != ' + before.active);
      if (now.top !== before.top) out.push(at + 'scrollingElement scrollTop ' + now.top + ' != ' + before.top);
    }
    return out;
  },
  async real_controls() {
    return eachView(['full', 'home'], VIEWPORTS, async view => {
      const out = await ev(`(function(){var U=__u,A=(window.__uxrAllow||{}).controls||{},out=[],NAT=/^(BUTTON|A|INPUT|LABEL|SUMMARY|SELECT)$/;
       Array.prototype.forEach.call(document.querySelectorAll('*'),function(e){var s=U.sel(e);if(NAT.test(e.tagName)||s in A)return;
        if(e.hasAttribute('onclick'))out.push(s+' <'+e.tagName.toLowerCase()+'> has onclick attribute');
        else if(e.getAttribute('role')==='button')out.push(s+' <'+e.tagName.toLowerCase()+'> role=button is not a native button');
        else if(typeof e.onclick==='function')out.push(s+' <'+e.tagName.toLowerCase()+'> has an onclick handler')});return out})()`);
      if (view !== 'home') return out;
      // :focus-visible ring on the first button of #home, #qs (inside the opened sheet) and #lock.
      for (const [box, pre, post] of [['home', '', ''], ['qs', 'opos.open()', 'opos.close()'], ['lock', 'oposUI.lock()', 'oposUI.unlock()']]) {
        if (pre) await ev(pre + ';new Promise(function(r){requestAnimationFrame(function(){setTimeout(r,60)})})');
        const ok = await ev(`(function(){var b=document.querySelector('#${box} button');if(!b||!__u.vis(b))return 'none';b.focus();return document.activeElement===b?'ok':'nofocus'})()`);
        if (ok !== 'ok') { out.push('#' + box + ' first button ' + (ok === 'none' ? 'not visible' : 'did not take focus')); if (post) await ev(post); continue; }
        await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Shift', code: 'ShiftLeft', windowsVirtualKeyCode: 16 });
        await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Shift', code: 'ShiftLeft', windowsVirtualKeyCode: 16 });
        const r = await ev(`(function(){var b=document.querySelector('#${box} button');b.blur();b.focus();var c=getComputedStyle(b);return {fv:b.matches(':focus-visible'),st:c.outlineStyle,w:parseFloat(c.outlineWidth),s:__u.sel(b)}})()`);
        if (!r.fv) out.push(r.s + ' does not match :focus-visible after keyboard focus');
        else if (r.st === 'none' || !(r.w >= 2)) out.push(r.s + ' :focus-visible outline ' + r.st + ' ' + r.w + 'px (need a style and >= 2px)');
        if (post) await ev(post);
      }
      return out;
    });
  },
  async fail_open() {
    // The new shell JS throws (BOOM); the base phone must still render state and take real taps and typing.
    const out = [], at = '360x640 boom ';
    await load('boom', 360, 640, false);
    if (!await ev('window.__uxrBoom>0')) return [at + 'fault injection never hit the shell JS'];
    if (!await waitFor(`/\\S/.test(document.getElementById('screen').textContent)&&!/Could not reach/.test(document.getElementById('screen').textContent)`))
      out.push(at + '#screen did not render state: "' + await ev(`document.getElementById('screen').textContent.slice(0,40)`) + '"');
    const tap = async sel => {
      const p = await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e||!__u.vis(e))return null;e.scrollIntoView({block:'center'});
        var r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,h=document.elementFromPoint(x,y);return {x:x,y:y,hit:!!h&&(h===e||e.contains(h))}})()`);
      if (!p) { out.push(at + sel + ' not visible'); return false; }
      if (!p.hit) { out.push(at + sel + ' covered by another element'); return false; }
      for (const type of ['mousePressed', 'mouseReleased'])
        await send('Input.dispatchMouseEvent', { type, x: p.x, y: p.y, button: 'left', clickCount: 1 });
      return true;
    };
    const sent = (a, k, v) => waitFor(`(window.__uxr.fetches||[]).some(function(x){return x.action===${JSON.stringify(a)}&&x.${k}===${JSON.stringify(v)}})`, 2000);
    if (await tap('#number')) await send('Input.insertText', { text: '5551234' });
    if (await tap('#controls>button[data-go=call]') && !await sent('call', 'number', '5551234')) out.push(at + 'Call tap sent no call request for the typed number');
    if (await tap('#msg')) await send('Input.insertText', { text: 'hi' });
    if (await tap('#controls>button[data-go=send]') && !await sent('send', 'text', 'hi')) out.push(at + 'Send tap sent no send request for the typed text');
    if (await tap('#meter-hit') && !await waitFor('opos.isOpen()', 1000)) out.push(at + '#meter-hit tap did not open the details sheet');
    return out;
  },
  async helper_lazy() {
    // Signal: before the tap #ask is hidden, #answer is empty, there is no #helperwait progress bar, and no
    // helper request (action ask/plan/explain/visible) has gone through fetch; no /api resource entry either.
    return eachView(['home'], VIEWPORTS, async () => {
      const out = [];
      const pre = await ev(`(function(){var a=document.getElementById('ask'),n=document.getElementById('answer'),H=${JSON.stringify(HELPER_ACTIONS)};
        var f=(window.__uxr&&__uxr.fetches||[]).filter(function(x){return H.indexOf(x.action)>=0}).map(function(x){return x.action});
        var r=performance.getEntriesByType('resource').filter(function(x){return /\\/api(\\?|$)/.test(x.name)}).map(function(x){return x.name});
        window.__opos_marker=1;return {ask:!!(a&&a.hidden),ans:n?n.textContent:'missing',wait:!!document.getElementById('helperwait'),f:f,r:r}})()`);
      if (!pre.ask) out.push('#ask visible before Helper was opened');
      if (pre.ans) out.push('#answer not empty before Helper was opened: "' + pre.ans.slice(0, 24) + '"');
      if (pre.wait) out.push('#helperwait present before Helper was opened');
      if (pre.f.length) out.push('fetch helper actions before open: ' + pre.f.join(','));
      if (pre.r.length) out.push('resource /api entries before open: ' + pre.r.join(','));
      const box = await ev(`(function(){var t=document.querySelector('#home [data-open=helper]');if(!t||!__u.vis(t))return null;var r=t.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
      if (!box) return out.concat(['#home>[data-open=helper] Helper tile not visible']);
      for (const type of ['mousePressed', 'mouseReleased'])
        await send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y, button: 'left', clickCount: 1 });
      const t0 = Date.now();
      const shown = await waitFor(`document.getElementById('phone').dataset.app==='helper'&&__u.vis(document.getElementById('ask'))`, 2000);
      if (!shown) out.push('#ask Helper view not visible ' + (Date.now() - t0) + 'ms after one tap (app=' + await ev(`document.getElementById('phone').dataset.app||''`) + ')');
      if (await ev('window.__opos_marker') !== 1) out.push('window marker lost (page reloaded) after Helper tap');
      const asked = await ev(`(window.__uxr.fetches||[]).filter(function(x){return /^(ask|plan|explain)$/.test(x.action)}).length`);
      if (asked) out.push('opening Helper sent ' + asked + ' question request(s) on its own');
      return out;
    });
  },
};

// UXP20 helper: exercise the existing live page without navigating on rotation.
CHECKS.continuity = async function() {
  const out = [];
  await load('home', 720, 1280, false);
  await ev("oposUI.openApp('messages');document.getElementById('msg').focus();window.__continuity=1");
  await send('Input.insertText', {text:'draft survives rotation'});
  await ev("document.getElementById('msg').setSelectionRange(6,14,'backward')");
  await sleep(50);
  const snap = `JSON.stringify({marker:window.__continuity,id:document.activeElement.id,
    value:document.getElementById('msg').value,start:document.getElementById('msg').selectionStart,
    end:document.getElementById('msg').selectionEnd,direction:document.getElementById('msg').selectionDirection})`;
  const before = await ev(snap);
  for (const [w,h] of [[1280,720],[720,1280]]) {
    await resize(w,h);
    if (await ev(snap) !== before) out.push(w+'x'+h+' draft/caret/focus/reload changed');
  }
  await ev("opos.open()");
  await sleep(100);
  // Constrain the existing scroller so the test has a nonzero, valid offset in both sizes.
  await ev("document.getElementById('sheet').style.height='180px';document.getElementById('sheet').scrollTop=60");
  await sleep(50);
  const top = await ev("document.getElementById('sheet').scrollTop");
  if (!(top>0)) out.push('sheet fixture did not scroll');
  for (const [w,h] of [[1280,720],[720,1280]]) {
    await resize(w,h);
    if (await ev("document.getElementById('sheet').scrollTop") !== top) out.push('sheet scroll lost');
    if (!await ev("opos.isOpen()&&document.getElementById('lock').hidden")) out.push('overlay state changed');
  }
  async function key(key,code) {
    await send('Input.dispatchKeyEvent',{type:'keyDown',key,code});
    await send('Input.dispatchKeyEvent',{type:'keyUp',key,code});
  }
  await key('Escape','Escape');
  if (!await ev("!opos.isOpen()&&document.getElementById('phone').dataset.app==='messages'")) out.push('Escape skipped shade level');
  await key('Escape','Escape');
  if (!await ev("document.getElementById('phone').dataset.app==='home'")) out.push('Escape did not return home');
  await ev("document.getElementById('allapps').click();opos.open()");
  await sleep(50);
  await key('Escape','Escape');
  if (!await ev("!opos.isOpen()&&document.getElementById('phone').dataset.allapps==='1'")) out.push('Escape skipped All apps level');
  await key('Escape','Escape');
  if (!await ev("!document.getElementById('phone').dataset.allapps")) out.push('Escape did not close All apps');
  await ev("oposUI.openApp('phone');document.getElementById('number').value='555';document.getElementById('number').focus()");
  const dials = await ev("__uxr.fetches.filter(x=>x.action==='call').length");
  await key('Enter','Enter');
  await sleep(50);
  if (await ev("__uxr.fetches.filter(x=>x.action==='call').length") !== dials) out.push('Enter in dialer must not dial');
  await ev("oposUI.openApp('messages');document.getElementById('msg').focus()");
  if (!await ev("document.activeElement.id==='msg'&&document.getElementById('msg').getClientRects().length>0")) out.push('compose field must be visible and focused');
  const count = await ev("__uxr.fetches.filter(x=>x.action==='send').length");
  await key('Enter','Enter');
  await sleep(50);
  if (await ev("__uxr.fetches.filter(x=>x.action==='send').length") !== count+1) out.push('Enter must send exactly once');
  await ev("oposUI.lock()");
  for (const [w,h] of [[1280,720],[720,1280]]) {
    await resize(w,h);
    if (!await ev("!document.getElementById('lock').hidden&&document.getElementById('phone').inert&&!opos.isOpen()")) out.push('lock changed on rotation');
  }
  return out;
};

(async () => {
  if (!CHECKS[check]) throw new Error('unknown check ' + check);
  const close = await connect();
  let violations, targetId;
  const timer = setTimeout(() => { process.stderr.write('timed out\n'); close(); process.exit(3); }, 25000);
  try {
    ({ targetId } = await sendTo(null, 'Target.createTarget', { url: 'about:blank' }));
    ({ sessionId: session } = await sendTo(null, 'Target.attachToTarget', { targetId, flatten: true }));
    await send('Page.enable');
    await send('Runtime.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', { source: BOOM + STUB +'window.__uxrAllow=' + JSON.stringify(STATE.allow || {}) + ';' });
    violations = await CHECKS[check]();
  } finally {
    clearTimeout(timer);
    if (targetId) await Promise.race([sendTo(null, 'Target.closeTarget', { targetId }).catch(() => {}), sleep(1000)]);
    close();
  }
  process.stdout.write(JSON.stringify({ violations }) + '\n', () => process.exit(0));
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(2); });
