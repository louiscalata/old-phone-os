// SPDX-License-Identifier: GPL-3.0-or-later
// Drive one headless Chromium (DevTools port) through a JSON plan.
// Usage: node tests/apps_cdp.js <port> <plan.json>
// The plan is { "cases": [ { "url", "width", "height", "theme", "scale", "hand", "script" } ] }.
// script is a function body that returns an array of problem strings. Prints {"violations":[...]}.
'use strict';
const fs = require('fs');
const [port, planFile] = process.argv.slice(2);
const plan = JSON.parse(fs.readFileSync(planFile, 'utf8'));

let raw, session, seq = 0;
const pending = new Map();
function onMessage(d) {
  const p = d.id && pending.get(d.id);
  if (p) {
    pending.delete(d.id);
    d.error ? p.rej(new Error(p.method + ': ' + (d.error.message || 'devtools'))) : p.res(d.result);
  }
}
function send(method, params, sessionId) {
  return new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, { res, rej, method });
    const msg = { id, method, params: params || {} };
    if (sessionId) msg.sessionId = sessionId;
    raw(JSON.stringify(msg));
  });
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
async function ev(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, session);
  if (r.exceptionDetails) {
    const x = r.exceptionDetails;
    throw new Error((x.exception && x.exception.description) || x.text || 'page error');
  }
  return r.result.value;
}

(async () => {
  const version = await (await fetch('http://127.0.0.1:' + port + '/json/version')).json();
  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('websocket failed')); });
  raw = s => ws.send(s);
  ws.onmessage = m => onMessage(JSON.parse(m.data));
  const created = await send('Target.createTarget', { url: 'about:blank' });
  const attached = await send('Target.attachToTarget', { targetId: created.targetId, flatten: true });
  session = attached.sessionId;
  await send('Page.enable', {}, session);
  await send('Runtime.enable', {}, session);
  const violations = [];
  for (const c of plan.cases) {
    const theme = c.theme === 'dark' ? 'dark' : 'light';
    const hand = c.hand === 'left' ? 'left' : 'right';
    const scale = c.scale === 2 ? 2 : 1;
    const label = c.width + 'x' + c.height + ' ' + theme + (scale === 2 ? ' 200%' : '') + (hand === 'left' ? ' hand-left' : '');
    await send('Emulation.setDeviceMetricsOverride', {
      width: c.width, height: c.height, deviceScaleFactor: 1, mobile: c.width < 800,
    }, session);
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: theme }] }, session);
    await send('Page.navigate', { url: c.url }, session);
    const deadline = Date.now() + 8000;
    let ready = false;
    while (Date.now() < deadline) {
      try { ready = await ev('document.readyState==="complete"'); } catch (e) { ready = false; }
      if (ready) break;
      await sleep(40);
    }
    await ev("document.documentElement.setAttribute('data-theme','" + theme + "');document.documentElement.setAttribute('data-hand','" + hand + "');document.documentElement.style.fontSize='" + (scale * 100) + "%'");
    await sleep(80);
    let found = [];
    let next = c.next || '';
    try {
      const value = await ev('(function(){\n' + c.script + '\n})()');
      if (Array.isArray(value)) found = value;
      else if (value && typeof value === 'object') {
        found = Array.isArray(value.problems) ? value.problems : ['script object had no problems list'];
        if (typeof value.next === 'string') next = value.next;
      } else found = ['script did not return a list'];
    } catch (e) {
      found = [String(e.message || e)];
    }
    if (next && c.after) {
      await send('Page.navigate', { url: next }, session);
      const end = Date.now() + 8000;
      let ready = false;
      while (Date.now() < end) {
        try { ready = await ev('document.readyState==="complete"'); } catch (e) { ready = false; }
        if (ready) break;
        await sleep(40);
      }
      await ev("document.documentElement.setAttribute('data-theme','" + theme + "');document.documentElement.setAttribute('data-hand','" + hand + "');document.documentElement.style.fontSize='" + (scale * 100) + "%'");
      await sleep(80);
      try {
        const more = await ev('(function(){\n' + c.after + '\n})()');
        if (Array.isArray(more)) found = found.concat(more);
        else found.push('follow-up script did not return a list');
      } catch (e) {
        found.push(String(e.message || e));
      }
    }
    for (const item of found) violations.push(label + ' ' + item);
  }
  try { ws.close(); } catch (e) {}
  process.stdout.write(JSON.stringify({ violations }) + '\n');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
