// SPDX-License-Identifier: GPL-3.0-or-later
// Actual poller and Shade wrapper in a controlled VM; no browser, server or phone.
'use strict';
const assert = require('node:assert/strict'), vm = require('node:vm');
const payload = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const flush = async () => { for (let i=0;i<20;i++) await Promise.resolve(); };
async function world(options={}) {
  let clock=0,next=0,reads=0,style='',cleanupWhileObserved=0,lateQueued=false;
  const timers=new Map(), observers=new Set();
  function mutate(target,attributeName='') {
    for(const mo of observers)if(mo.active){
      mo.records.push({target,type:attributeName?'attributes':'childList',attributeName});
      if(target.id==='sheet'||target.id==='animation-cleanup')cleanupWhileObserved++;
      if(!mo.scheduled){mo.scheduled=true;Promise.resolve().then(()=>{mo.scheduled=false;const records=mo.takeRecords();if(records.length)mo.callback(records);});}
    }
  }
  const sheet={id:'sheet',nodeType:1,style:{},addEventListener(){}};
  Object.defineProperty(sheet.style,'willChange',{get:()=>style,set:value=>{if(style!==value){style=value;mutate(sheet,'style');}}});
  function advance(ms){clock+=ms;for(const [id,t] of [...timers])if(t.due<=clock){timers.delete(id);t.fn();}}
  const setTimeout=(fn,ms)=>{const id=++next;timers.set(id,{fn,due:clock+ms});return id;};
  class Observer {
    constructor(callback){this.callback=callback;this.records=[];observers.add(this);}
    observe(document,settings){assert.equal(JSON.stringify(settings),JSON.stringify({subtree:true,childList:true,attributes:true,characterData:true}));this.active=true;}
    takeRecords(){const result=this.records;this.records=[];return result;}
    disconnect(){this.active=false;}
  }
  const context={running:false,navigator:{onLine:true},Promise,Set,AbortSignal:{timeout:()=>null},Date:{now:()=>clock},performance:{now:()=>clock},addEventListener(){},setTimeout,clearTimeout:id=>timers.delete(id),requestAnimationFrame:fn=>{advance(100);Promise.resolve().then(fn);},MutationObserver:Observer,
    document:{getElementById:id=>id==='sheet'?sheet:null,querySelector:()=>({content:'fixture-token'}),getAnimations:()=>{if(options.lateCleanup&&reads&&!lateQueued){lateQueued=true;setTimeout(()=>mutate({id:'animation-cleanup',nodeType:1},'style'),50);}return options.busy?[{playState:'running'}]:[];}},G:id=>id==='sheet'?sheet:null,
    render:async()=>{for(const id of ['status','battery','helper'])mutate({id,nodeType:1});},
    fetch:async()=>{reads++;if(options.neverFetch&&reads===1)await new Promise(()=>{});if(options.pending&&reads===1)await new Promise(resolve=>setTimeout(resolve,100));else advance(150);return {json:async()=>({running:false,poll_ms:1000,battery:{percent:options.changed&&reads>=3?40:50}})};},
    openSheet(){},closeSheet(){},opos:{}}
  context.window=context;
  vm.createContext(context);
  let actualPoller=payload.poller;
  // These two cases adapt the independent actual-source lifecycle probe into this observer fixture.
  if(options.unconditionalRender){const gate='if(!s||s.error||stateKey(s)!==seen)';assert.equal(actualPoller.split(gate).length,2);actualPoller=actualPoller.replace(gate,'if(true)');}
  vm.runInContext(actualPoller,context);vm.runInContext(payload.shade,context);
  context.opos.open();
  if(options.pending||options.neverFetch){advance(1000);await flush();assert.equal(context.__uxp23Polls.active.size,1);}
  const originalFetch=context.fetchState,originalArm=context.armPoll;
  const output=await vm.runInContext(payload.idle,context);await flush();
  assert.equal(context.fetchState,originalFetch,'fetch instrumentation restored');
  assert.equal(context.armPoll,originalArm,'poll scheduling restored');
  assert.equal([...observers].some(mo=>mo.active),false,'observer disconnected');
  return {output:Array.from(output),reads,calls:context.__uxp23Polls.calls,active:context.__uxp23Polls.active.size,cleanupWhileObserved,clock};
}
(async()=>{
  const positive=await world();assert.deepEqual(positive.output,[]);assert.equal(positive.calls,4);assert.equal(positive.cleanupWhileObserved,0);
  const inflight=await world({pending:true});assert.deepEqual(inflight.output,[]);assert.equal(inflight.active,0);assert.equal(inflight.calls,5);assert.equal(inflight.cleanupWhileObserved,0);
  const late=await world({lateCleanup:true});assert.deepEqual(late.output,[]);assert.equal(late.cleanupWhileObserved,0);
  const changed=await world({changed:true});assert(changed.output.some(s=>s.includes('input key changed')&&s.includes('battery')));assert(changed.output.some(s=>s.includes('DOM records')));
  const write=await world({unconditionalRender:true});assert(write.output.some(s=>s.includes('9 DOM records')&&s.includes('#status')));assert(!write.output.some(s=>s.includes('input key changed')));
  const busy=await world({busy:true});assert(busy.output.some(s=>s.includes('readiness timeout')&&s.includes('animations')));assert(busy.clock>=2000&&busy.clock<2500);
  const stuck=await world({neverFetch:true});assert(stuck.output.some(s=>s.includes('readiness timeout')&&s.includes('\"polls\":1')));assert.equal(stuck.active,1);assert(stuck.clock>=3000&&stuck.clock<3500);
  console.log(JSON.stringify({positive,inflight,late,changed,write,busy,stuck}));
  console.log('actual poller and Shade fixtures passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
