// SPDX-License-Identifier: GPL-3.0-or-later
// Local synthetic shell check. Own Chromium process; protocol-only shutdown.
'use strict';
const fs = require('fs'), path = require('path'), os = require('os');
const {spawn} = require('child_process');
const [chrome, url, stateFile] = process.argv.slice(2);
const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'jaryo-motion-'));
const child = spawn(chrome, ['--headless', '--no-sandbox', '--disable-gpu',
  '--remote-debugging-pipe', '--no-first-run', '--no-default-browser-check',
  '--user-data-dir=' + profile, 'about:blank'], {stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe']});
let seq = 0, buffer = Buffer.alloc(0), session;
const pending = new Map();
const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({code, signal})));
child.stdio[4].on('data', part => {
  buffer = Buffer.concat([buffer, part]);
  for (let pos; (pos = buffer.indexOf(0)) >= 0; buffer = buffer.subarray(pos + 1)) {
    const msg = JSON.parse(buffer.subarray(0, pos));
    const item = pending.get(msg.id);
    if (item) {pending.delete(msg.id); clearTimeout(item.timer); msg.error ? item.reject(new Error(msg.error.message)) : item.resolve(msg.result);}
  }
});
function send(method, params = {}, sid = session) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => {pending.delete(id); reject(new Error('CDP timeout: ' + method));}, 15000);
    pending.set(id, {resolve, reject, timer});
    child.stdio[3].write(JSON.stringify({id, method, params, ...(sid ? {sessionId: sid} : {})}) + '\0');
  });
}
async function ev(expression) {
  const r = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
async function main() {
  const violations = [];
  const target = await send('Target.createTarget', {url: 'about:blank'});
  session = (await send('Target.attachToTarget', {targetId: target.targetId, flatten: true})).sessionId;
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {width: 360, height: 640, deviceScaleFactor: 1, mobile: true});
  await send('Page.addScriptToEvaluateOnNewDocument', {source: `window.fetch=()=>Promise.resolve(new Response(${JSON.stringify(JSON.stringify(state))},{status:200}));`});
  await send('Page.navigate', {url: url + '?lock=0'});
  const deadline = Date.now() + 5000;
  while (!await ev("!!(window.oposUI&&window.jaryo&&document.getElementById('qs'))")) {
    if (Date.now() > deadline) throw new Error('shell did not initialize');
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  for (const reduced of [false, true]) {
    await send('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: reduced ? 'reduce' : 'no-preference'}]});
    const result = await ev(`(async function(){
      var problems=[],p=document.getElementById('phone'),article=document.getElementById('threads');
      oposUI.openApp('home');await new Promise(r=>setTimeout(r,300));
      var tile=document.querySelector('#home [data-open=messages]');
      var original=Element.prototype.animate,records=[];
      Element.prototype.animate=function(k,o){var a=original.call(this,k,o);a.pause();records.push({el:this,k:k,a:a});return a};
      var raf=window.requestAnimationFrame,queue=[];
      window.requestAnimationFrame=f=>{queue.push(f);return queue.length};
      try{
        oposUI.openApp('messages');if(p.dataset.app!=='messages')problems.push('open state was deferred');
        var from=article.getBoundingClientRect();
        oposUI.openApp('home');if(p.dataset.app!=='home')problems.push('back state was deferred');
        var target=tile.getBoundingClientRect();
        while(queue.length)queue.shift()();
        if(records.length!==2)problems.push('stale batch survived: '+records.length);
        var outgoing=records.find(x=>x.el===article),incoming=records.find(x=>x.el===tile);
        if(!outgoing||!incoming)problems.push('wrong moving elements');
        records.forEach(x=>{if(x.a.effect.getTiming().duration!==(${reduced}?120:240))problems.push('wrong motion duration')});
        if(outgoing){
          var a=outgoing.a;a.currentTime=0;var start=article.getBoundingClientRect();
          if(getComputedStyle(article).display==='none'||!start.width||!start.height)problems.push('outgoing article hidden');
          if(!article.inert)problems.push('outgoing article can intercept home interaction');
          if(Math.abs(start.width-from.width)>1||Math.abs(start.height-from.height)>1)problems.push('reverse start is not outgoing geometry');
          if(!${reduced}){
            a.currentTime=239.999;var end=article.getBoundingClientRect();
            for(var key of ['left','top','width','height'])if(Math.abs(end[key]-target[key])>1)problems.push('reverse target '+key+' '+end[key]+' != '+target[key]);
          }
        }
        records.forEach(x=>x.k.forEach(k=>Object.keys(k).forEach(key=>{if(!(${reduced}?[ 'opacity']:['opacity','transform']).includes(key))problems.push('keyframe property '+key)})));
        if(document.activeElement!==tile)problems.push('return tile not focused');
        document.getElementById('allapps').click();if(!p.dataset.allapps)problems.push('All apps blocked during reverse');
        records.forEach(x=>x.a.finish());await Promise.all(records.map(x=>x.a.finished));await new Promise(r=>raf(()=>raf(r)));
        if(article.style.position||article.style.display||article.inert)problems.push('outgoing state not restored');
        if(getComputedStyle(article).display!=='none')problems.push('outgoing article remains visible after finish');
        // Reopening halfway through Back must measure the restored in-flow article,
        // never the temporary fixed box left by the interrupted reverse animation.
        oposUI.openApp('messages');while(queue.length)queue.shift()();
        records.forEach(x=>{if(x.a.playState!=='idle')x.a.finish()});
        await Promise.all(records.filter(x=>x.a.playState!=='idle').map(x=>x.a.finished));await new Promise(r=>raf(()=>raf(r)));
        oposUI.openApp('home');while(queue.length)queue.shift()();
        var reverse=records.filter(x=>x.el===article).slice(-1)[0];reverse.a.currentTime=${reduced}?60:120;
        var home=tile.getBoundingClientRect();
        oposUI.openApp('messages');while(queue.length)queue.shift()();
        if(reverse.a.playState!=='idle')problems.push('interrupted reverse not cancelled');
        if(article.style.position||article.inert)problems.push('reverse geometry survives reopen');
        var reopened=records.filter(x=>x.el===article).slice(-1)[0];reopened.a.currentTime=0;
        if(!${reduced}){var start=article.getBoundingClientRect();for(var key of ['left','top','width','height'])if(Math.abs(start[key]-home[key])>1)problems.push('reopen measured stale ghost '+key)}
        records.forEach(x=>{if(x.a.playState!=='idle')x.a.finish()});
        await Promise.all(records.filter(x=>x.a.playState!=='idle').map(x=>x.a.finished));await new Promise(r=>raf(()=>raf(r)));
      }finally{window.requestAnimationFrame=raf;Element.prototype.animate=original}
      return problems;
    })()`);
    violations.push(...result.map(x => (reduced ? 'reduced: ' : 'full: ') + x));
  }
  console.log(JSON.stringify({violations}));
}
(async () => {
  let error;
  try {await main();} catch (e) {error = e;}
  finally {
    try {await send('Browser.close', {}, null);} catch (e) {if (child.exitCode === null) error ||= e;}
    const result = await exited;
    for (const item of pending.values()) clearTimeout(item.timer);
    pending.clear();
    if (result.signal || result.code !== 0) error ||= new Error('Chromium exit ' + JSON.stringify(result));
    fs.rmSync(profile, {recursive: true, force: true});
  }
  if (error) {console.error(error.stack);process.exitCode = 1;}
})();
