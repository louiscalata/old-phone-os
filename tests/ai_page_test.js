// SPDX-License-Identifier: GPL-3.0-or-later
// Run the rendered AI document script in a small DOM, with no sockets or browser.
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const clone = obj => JSON.parse(JSON.stringify(obj));
const settle = async () => { for(let i=0;i<20;i++) await Promise.resolve(); };
function world(search='') {
  const nodes = new Map(), requests = [], timers = new Map(), listeners = new Map();
  let now=0, seq=0, focused=null;
  class Element {
    constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attrs={};this.hidden=false;this.disabled=false;this.value='';this.text='';this.parentNode=null;this.scrollHeight=100;this.clientHeight=100;this.top=0;}
    set textContent(value) {this.writes=(this.writes||0)+1;this.text=String(value);this.children=[];this.scrollHeight=Math.max(100,this.text.length);}
    get textContent() {return this.text;}
    set scrollTop(value) {this.top=Math.max(0,Math.min(value,this.scrollHeight-this.clientHeight));}
    get scrollTop() {return this.top;}
    set innerHTML(value) {throw new Error('Markup insertion is forbidden');}
    appendChild(child) {return this.insertBefore(child,null);}
    insertBefore(child,before) {
      if(child.parentNode)child.parentNode.children.splice(child.parentNode.children.indexOf(child),1);
      this.children.splice(before?this.children.indexOf(before):this.children.length,0,child);child.parentNode=this;return child;
    }
    setAttribute(key,value) {this.attrs[key]=value;}
    focus() {focused=this;}
  }
  const ids=['list','thumb','ai-title','home','nav','controls','question','ask','presets','err','enrol','enrol-text','allow','deny','reopen','forget',
    'phone-pane','phone-title','phone-model','phone-state','phone-speed','phone-answer','phone-stop',
    'gpu-pane','gpu-title','gpu-model','gpu-state','gpu-speed','gpu-answer','gpu-stop','gpu-disclosure','gpu-cable','link-controls'];
  for(const id of ids)nodes.set(id,new Element(id==='question'?'input':'div'));
  const get=id=>{assert.ok(nodes.has(id),'known DOM id '+id);return nodes.get(id)};
  for(const id of ['ai-title','controls','phone-pane'])get('list').appendChild(get(id));
  for(const id of ['gpu-pane','nav'])get('thumb').appendChild(get(id));
  get('nav').appendChild(get('home'));
  for(const n of ['phone','gpu']){
    for(const suffix of ['title','model','speed','state',...(n==='gpu'?['cable','disclosure']:[]),'answer','stop'])
      get(n+'-pane').appendChild(get(n+'-'+suffix));
    get(n+'-title').textContent=n==='phone'?'Phone':'PC GPU';
    get(n+'-stop').textContent=n==='phone'?'Stop phone answer':'Stop GPU answer';
  }
  get('gpu-pane').appendChild(get('link-controls'));
  for(const id of ['enrol','reopen','forget'])get('link-controls').appendChild(get(id));
  for(const id of ['enrol-text','deny','allow'])get('enrol').appendChild(get(id));
  get('gpu-disclosure').textContent='This PC can see what you ask here.';
  for(const [id,label] of Object.entries({deny:'Not now',allow:'Allow',reopen:'Allow PC requests again',forget:'Forget this PC'}))get(id).textContent=label;
  const document={hidden:false,documentElement:new Element('html'),getElementById:get,createElement:tag=>new Element(tag),
    get activeElement(){return focused},
    querySelector:()=>({content:'fixture-token'}),addEventListener:(name,fn)=>listeners.set(name,fn)};
  const context=vm.createContext({document,location:{search},URLSearchParams,AbortController,performance:{now:()=>now},
    setTimeout:(fn,delay)=>{const id=++seq;timers.set(id,{fn,at:now+delay});return id},clearTimeout:id=>timers.delete(id),
    fetch:(url,options)=>new Promise((resolve,reject)=>{
      const req={url,options,at:now,resolve:(s,status=200)=>resolve({status,json:()=>Promise.resolve(clone(s))}),reject,
        resolveInvalidJSON:status=>resolve({status,json:()=>Promise.reject(new SyntaxError('No JSON body'))}),
        resolveBody:()=>resolve({status:200,json:()=>new Promise(bodyResolve=>{req.resolve=s=>bodyResolve(clone(s))})})};
      requests.push(req);
    })});
  vm.runInContext(fixture.script,context);
  async function tick(){assert.ok(timers.size,'a scheduled timer');const [id,item]=[...timers.entries()].sort((a,b)=>a[1].at-b[1].at)[0];timers.delete(id);now=item.at;item.fn();await settle();}
  async function reply(s,status=200){assert.equal(requests.length,1,'one in-flight request');requests.shift().resolve(s,status);await settle();}
  async function show(s){await tick();await reply(s);}
  return {get,document,requests,timers,tick,reply,show,hidden:value=>{document.hidden=value;listeners.get('visibilitychange')()},focus:()=>focused};
}
function snapshot(rev=1){const s=clone(fixture.snapshot);s.rev=rev;s.phone.answer=null;s.gpu.answer=null;return s;}
function answer(state='answering'){return {...clone(fixture.snapshot.gpu.answer),state,tokens:21,tps:42.125,error:null,where:'Demo PC'};}
function pc(state='ready'){return {label:'Demo PC',live:true,state,gpu:{name:'Demo GPU'},copied:41,total:100,error:null};}

test('startup read faults and recovery warnings stay visible and block GPU asks',async()=>{
 for(const notice of ['The phone could not read a saved answer. Restore access and restart AI mode.',
                      'A saved answer was damaged and moved aside. Restart AI mode to continue.']){
  for(const search of ['', '?pane=gpu', '?pane=phone']){
   const w=world(search),s=snapshot();s.gpu.link.pc=pc();s.gpu.link.storage='error';
   s.gpu.link.storage_notice=notice;s.gpu.ready=false;
   await w.show(s);
   assert.equal(w.get('gpu-speed').textContent,notice);
   // Existing page-script behavior; waiting does not clear a startup notice.
   assert.equal(w.get('gpu-cable').textContent,notice+' Wait before unplugging.');
   const blocked=search!=='?pane=phone';
   assert.equal(w.get('ask').disabled,blocked);
   for(const button of w.get('presets').children)assert.equal(button.disabled,blocked);
   if(!blocked){
    w.get('question').value='Phone only';w.get('ask').onclick();await w.tick();
    assert.deepEqual(JSON.parse(w.requests[0].options.body).panes,['phone']);
   }
  }
 }
});

test('answer markup remains literal and every visible field comes from one snapshot',async()=>{
 const w=world(),s=snapshot();s.gpu.answer=clone(fixture.snapshot.gpu.answer);s.gpu.answer.tokens=21;s.gpu.answer.tps=42.125;s.gpu.answer.where='Demo PC';
 s.gpu.link.pc=pc();await w.show(s);
 assert.equal(w.get('gpu-answer').textContent,'<script>attack()</script><b>literal</b>');
 assert.equal(w.get('gpu-answer').children.length,0);
 assert.equal(w.get('gpu-state').textContent,'Done · 21 tokens');
 assert.equal(w.get('gpu-speed').textContent,'42.125 tokens per second');
 assert.equal(w.get('gpu-model').textContent,'Large model · Demo PC · Demo GPU');
 const old=snapshot(0);old.phone.model='stale';old.gpu.answer=answer('failed');await w.show(old);
 assert.equal(w.get('gpu-state').textContent,'Done · 21 tokens');assert.notEqual(w.get('phone-model').textContent,'stale');
 const restart=snapshot(0);restart.boot='ffffffff';restart.gpu.answer=answer('paused');restart.gpu.answer.text='prefix';await w.show(restart);
 assert.equal(w.get('gpu-state').textContent,'Paused. It continues when the PC is ready. · 21 tokens');assert.equal(w.get('gpu-answer').textContent,'prefix');
});

test('single-pane addresses keep both DOM panes and move controls to the visible pane',async()=>{
 for(const name of ['phone','gpu']){
  const w=world('?pane='+name);await w.show(snapshot());
  assert.equal(w.document.documentElement.attrs['data-pane'],name);
  assert.equal(w.get(name==='phone'?'thumb':'list').hidden,true);
  assert.ok(w.get('phone-pane'));assert.ok(w.get('gpu-pane'));
  const visible=w.get(name==='phone'?'list':'thumb');
  assert.deepEqual(visible.children.map(n=>[...['ai-title','controls',name+'-pane','nav']].find(id=>w.get(id)===n)),
    ['ai-title','controls',name+'-pane','nav']);
  w.get('question').value='Public question';w.get('ask').onclick();await w.tick();
  assert.deepEqual(JSON.parse(w.requests[0].options.body),{action:'ask',question:'Public question',panes:[name]});
 }
 const w=world('?pane=unknown');assert.equal(w.document.documentElement.attrs['data-pane'],undefined);
});

test('ask and all three one-tap presets send the exact question to both panes',async()=>{
 const w=world(),s=snapshot();await w.show(s);
 assert.equal(w.get('presets').children.length,3);
 const firstButton=w.get('presets').children[0];
 for(let i=0;i<3;i++){
  w.get('presets').children[i].onclick();await w.tick();
  assert.equal(w.requests[0].url,'/api/ai');assert.equal(w.requests[0].options.headers['X-OPOS-Token'],'fixture-token');
  assert.equal(w.get('presets').children[i].textContent,s.presets[i].label);
  assert.ok(s.presets[i].label.length<=30);
  assert.equal(w.get('question').value,s.presets[i].question);
  assert.deepEqual(JSON.parse(w.requests[0].options.body),{action:'ask',question:s.presets[i].question,panes:['phone','gpu']});
  await w.reply({...s,rev:i+2});
 }
 assert.equal(w.get('presets').children[0],firstButton,'polling preserves keyboard focus targets');
 assert.match(s.presets[2].question,/twenty numbered sections/);assert.match(s.presets[2].question,/eighty words/);
 let prevented=false;w.get('question').value='Typed';w.get('question').onkeydown({key:'Enter',preventDefault(){prevented=true}});
 assert.equal(prevented,true);await w.tick();assert.equal(JSON.parse(w.requests[0].options.body).question,'Typed');
});

test('request cadence includes actions and sends nothing while hidden',async()=>{
 const w=world(),s=snapshot();await w.tick();assert.equal(w.requests[0].at,0);await w.reply(s);
 w.get('ask').onclick();await w.tick();assert.equal(w.requests[0].at,2000,'idle actions share the request limit');
 s.rev++;s.phone.answer=answer();await w.reply(s);
 await w.tick();assert.equal(w.requests[0].at,2100);
 w.get('gpu-stop').onclick=()=>{};
 w.hidden(true);assert.equal(w.timers.size,1,'in-flight request keeps its deadline');await w.reply({...s,rev:s.rev+1});
 assert.equal(w.timers.size,0);w.get('ask').onclick();assert.equal(w.timers.size,0);assert.equal(w.requests.length,0);
 w.hidden(false);await w.tick();assert.equal(w.requests.length,1);
 w.get('ask').onclick();assert.equal(w.timers.size,1,'only the in-flight deadline remains');
 await w.reply({...s,rev:s.rev+2});await w.tick();assert.equal(w.requests[0].at,2300);
});

for(const method of ['GET','POST'])test('a locked '+method+' immediately redacts answers, drops actions and keeps polling',async()=>{
 const w=world(),s=snapshot();
 for(const name of ['phone','gpu']){
  s[name].answer=answer();s[name].answer.text='private '+name+' answer visible before lock';
 }
 await w.show(s);
 for(const name of ['phone','gpu'])assert.equal(w.get(name+'-answer').textContent,s[name].answer.text);
 if(method==='POST')w.get('gpu-stop').onclick();
 await w.tick();assert.equal(w.requests[0].options.method,method);
 // Queue mutations while the request is in flight: none may survive the lock.
 w.get('gpu-stop').onclick();w.get('phone-stop').onclick();
 await w.reply({error:'locked'},403);
 // Assert before advancing any timer or supplying a locked GET snapshot.
 function domText(node){return node.textContent+node.children.map(domText).join('')}
 for(const id of ['list','thumb'])assert.doesNotMatch(domText(w.get(id)),/private .* answer visible before lock/);
 for(const name of ['phone','gpu']){
  assert.equal(w.get(name+'-answer').textContent,'');
  assert.equal(w.get(name+'-speed').textContent,'Unlock the phone to use AI mode.');
  assert.equal(w.get(name+'-model').textContent,'');
  assert.equal(w.get(name+'-state').textContent,'');
  assert.equal(w.get(name+'-stop').hidden,true);
 }
 assert.equal(w.get('controls').hidden,true);
 assert.equal(w.get('err').textContent,'locked');
 assert.equal(w.timers.size,1,'the locked refusal must leave the next poll scheduled');
 await w.tick();assert.equal(w.requests[0].options.method,'GET');
 await w.reply({locked:true});
 assert.equal(w.timers.size,1,'locked snapshots keep polling for unlock');
 await w.tick();assert.equal(w.requests[0].options.method,'GET');
 const unlocked=snapshot(s.rev+1);await w.reply(unlocked);
 assert.equal(w.get('controls').hidden,false);
 assert.equal(w.get('err').textContent,'');
 assert.equal(w.get('gpu-speed').textContent,'Waiting for the PC');
 await w.tick();assert.equal(w.requests[0].options.method,'GET','no queued mutation replays after unlock');
 await w.reply(unlocked);
});

test('a 403 without JSON or with an unexpected error keeps polling',async()=>{
 for(const method of ['GET','POST'])for(const body of [undefined,null,{}, {error:'unexpected'}]){
  const w=world();await w.show(snapshot());
  if(method==='POST')w.get('ask').onclick();
  await w.tick();assert.equal(w.requests[0].options.method,method);
  if(body===undefined){w.requests.shift().resolveInvalidJSON(403);await settle()}
  else await w.reply(body,403);
  assert.equal(w.get('err').textContent,body&&body.error||'AI mode could not update.');
  assert.equal(w.timers.size,1,'only a token refusal stops polling');
  await w.tick();assert.equal(w.requests[0].options.method,'GET');await w.reply(snapshot(2));
  assert.equal(w.get('err').textContent,'');assert.equal(w.timers.size,1);
 }
});

test('a forbidden poll or post explains that the page needs reloading',async()=>{
 for(const method of ['GET','POST']){
  const w=world();await w.show(snapshot());
  if(method==='POST')w.get('ask').onclick();
  await w.tick();assert.equal(w.requests[0].options.method,method);
  await w.reply({error:'forbidden'},403);
  assert.equal(w.get('err').textContent,'This page is out of date. Reload it, then try again.');
 }
});

test('a forbidden poll or post stops polling and queued actions until reload',async()=>{
 for(const method of ['GET','POST']){
  const w=world();await w.show(snapshot());
  if(method==='POST')w.get('ask').onclick();
  await w.tick();assert.equal(w.requests[0].options.method,method);
  w.get('ask').onclick();
  await w.reply({error:'forbidden'},403);
  assert.equal(w.timers.size,0,'no next poll or queued action');
  w.hidden(true);w.hidden(false);w.get('ask').onclick();
  assert.equal(w.timers.size,0,'visibility and actions cannot restart stale polling');
  assert.equal(w.requests.length,0);
 }
});

test('pending enrolment uses safe focus and exact disclosure, with explicit link controls',async()=>{
 for(const typing of [false,true]){
 const w=world(),s=snapshot();w.get(typing?'question':'ask').focus();
 s.gpu.link.pending={label:'Demo PC',code:'482913',seconds:60};await w.show(s);
 assert.equal(w.get('enrol').hidden,false);assert.equal(w.focus(),w.get(typing?'question':'deny'));
 assert.equal(w.get('enrol-text').textContent,'Use the GPU of Demo PC? Code 482 913. This PC will see what you ask in AI mode and will keep a copy of the model file.');
 for(const action of ['deny','allow','reopen','forget']){
  w.get(action).onclick();await w.tick();
  assert.deepEqual(JSON.parse(w.requests[0].options.body),['allow','deny'].includes(action)?{action,code:'482913'}:{action});await w.reply(s);
 }
 s.rev++;s.gpu.link.pending=null;s.gpu.link.enrol='closed';await w.show(s);
 assert.equal(w.get('enrol').hidden,true);assert.equal(w.get('enrol-text').textContent,'');assert.equal(w.get('reopen').hidden,false);
 }
});

test('new enrolments preserve typing focus and still require an explicit Allow tap',async()=>{
 for(const tag of ['input','textarea']){
  const w=world();await w.show(snapshot());
  const field=tag==='input'?w.get('question'):w.document.createElement(tag);
  if(tag==='textarea')w.get('controls').appendChild(field);
  field.value='Question in progress';field.focus();
  let rev=1;
  for(const code of ['482913','590079']){
   const s=snapshot(++rev);s.gpu.link.pending={label:'Demo PC',code,seconds:60};
   await w.tick();assert.equal(w.requests[0].options.method,'GET');await w.reply(s);
   assert.equal(w.focus(),field,tag+' keeps focus');
   assert.equal(field.value,'Question in progress');
   assert.equal(w.get('enrol').hidden,false);
   assert.equal(w.requests.length,0,'no automatic approval or refusal');
  }
  w.get('allow').onclick();await w.tick();
  assert.deepEqual(JSON.parse(w.requests[0].options.body),{action:'allow',code:'590079'});
 }
});

test('queued enrolment answers retain the drawn code when another request replaces it',async()=>{
 for(const action of ['allow','deny'])for(const hidden of [false,true]){
  const w=world(),a=snapshot();a.gpu.link.pending={label:'PC A',code:'590079',seconds:1};await w.show(a);
  assert.match(w.get('enrol-text').textContent,/PC A\? Code 590 079\./);
  await w.tick();assert.equal(w.requests[0].options.method,'GET');
  w.get(action).onclick();
  if(hidden)w.hidden(true);
  const b=snapshot(2);b.gpu.link.pending={label:'PC B',code:'482913',seconds:60};await w.reply(b);
  assert.match(w.get('enrol-text').textContent,/PC B\? Code 482 913\./);
  assert.equal(w.get('enrol').hidden,false);assert.equal(w.focus(),w.get('deny'));
  if(hidden)w.hidden(false);
  await w.tick();assert.deepEqual(JSON.parse(w.requests[0].options.body),{action,code:'590079'});
  await w.reply(b);w.get(action).onclick();await w.tick();
  assert.deepEqual(JSON.parse(w.requests[0].options.body),{action,code:'482913'});
 }
});

test('each answer state has plain words, stop controls and reported or missing metrics',async()=>{
 const expected={queued:'Queued behind an earlier answer',waiting:'Waiting for the phone model',loading:'Loading the model',
  answering:'Answering',paused:'Paused',done:'Done',failed:'No model is available on this phone.',cancelled:'Stopped'};
 const w=world();let rev=0;
 for(const [state,words] of Object.entries(expected)){
  const s=snapshot(++rev);s.phone.answer=answer(state);s.phone.answer.error=state==='failed'?'no_model':null;s.phone.answer.tps=null;
  await w.show(s);assert.equal(w.get('phone-state').textContent,'21 tokens');
  assert.equal(w.get('phone-speed').textContent,words);
  assert.equal(w.get('phone-stop').hidden,['done','failed','cancelled'].includes(state));
 }
 const s=snapshot(++rev);s.gpu.answer=answer();await w.show(s);w.get('gpu-stop').onclick();await w.tick();
 assert.deepEqual(JSON.parse(w.requests[0].options.body),{action:'cancel',id:s.gpu.answer.id});await w.reply(s);
 s.rev++;s.gpu.answer=answer('done');s.gpu.answer.finish='length';s.gpu.answer.tokens=null;await w.show(s);
 assert.equal(w.get('gpu-state').textContent,'Done: the model reached its answer limit.');
});

test('link off, absent or dead PC, copy progress, loading and model failures are explained',async()=>{
 const w=world();let rev=0;
 for(const [state,expected] of [['copying','Copying the model'],['loading','Loading the model'],['stopping','Stopping'],['error','The PC could not run the model.']]){
  const s=snapshot(++rev);s.gpu.link.pc=pc(state);await w.show(s);
  assert.equal(w.get('gpu-speed').textContent,state==='copying'?'Copying the model: 41%':expected);
 }
 let s=snapshot(++rev);s.gpu.link.pc=pc();s.gpu.link.pc.live=false;await w.show(s);assert.equal(w.get('gpu-speed').textContent,'Waiting for the PC');
 s=snapshot(++rev);s.gpu.link=null;s.phone.ready=false;await w.show(s);assert.equal(w.get('gpu-speed').textContent,'GPU Link is off.');assert.equal(w.get('phone-speed').textContent,'No model is available on this phone.');
 for(const [state,expected] of [['checking','Checking the model file'],['error','The model file could not be read.'],['none','No GPU model file is available.']]){
  s=snapshot(++rev);s.gpu.link.pc=pc();s.gpu.link.model.state=state;await w.show(s);assert.equal(w.get('gpu-speed').textContent,expected);
 }
 s=snapshot(++rev);s.gpu.answer=answer('paused');s.gpu.link.pc=pc();s.gpu.link.pc.live=false;await w.show(s);
 const prefix=w.get('gpu-answer').textContent;s.rev++;s.gpu.answer.state='answering';s.gpu.link.pc.live=true;s.gpu.answer.text=prefix+' continued';await w.show(s);
 assert.equal(w.get('gpu-answer').textContent,prefix+' continued');assert.equal(w.get('gpu-state').textContent,'Answering · 21 tokens');
});

test('locked replies clear text and controls; errors and failed fetches remain readable',async()=>{
 const w=world(),s=snapshot();s.gpu.answer=answer();await w.show(s);await w.show({locked:true});
 assert.equal(w.get('phone-speed').textContent,'Unlock the phone to use AI mode.');assert.equal(w.get('gpu-answer').textContent,'');
 assert.equal(w.get('gpu-model').textContent,'');assert.equal(w.get('controls').hidden,true);assert.equal(w.get('forget').hidden,true);
 s.rev++;await w.show(s);assert.equal(w.get('controls').hidden,false);
 await w.tick();await w.reply({error:'Enter a question.'},400);assert.equal(w.get('err').textContent,'Enter a question.');
 await w.tick();w.requests.shift().reject(new Error('private detail'));await settle();assert.equal(w.get('err').textContent,'AI mode could not update.');
});

test('pane headers never repeat visible lines across link and answer states',async()=>{
 const w=world();let rev=0;
 function visibleLines(e){
  if(e.hidden)return [];
  return [...e.textContent.split('\n').filter(Boolean),...e.children.flatMap(visibleLines)];
 }
 const links=[null,{...snapshot().gpu.link,pc:null},
  {...snapshot().gpu.link,pending:{label:'Demo PC',code:'482913',seconds:60}},
  {...snapshot().gpu.link,pc:null,enrol:'closed'},
  ...['ready','copying','loading','stopping','error'].map(state=>({...snapshot().gpu.link,pc:pc(state)})),
  {...snapshot().gpu.link,pc:{...pc(),live:false}},
  ...['checking','error','none'].map(state=>({...snapshot().gpu.link,pc:pc(),model:{state}}))];
 for(const link of links){
  for(const state of [null,'waiting','queued','loading','answering','paused','done','failed','cancelled']){
   const s=snapshot(++rev);s.gpu.link=link;
   for(const n of ['phone','gpu']){s[n].answer=state?answer(state):null;if(state)s[n].answer.tps=null;}
   await w.show(s);
   for(const n of ['phone','gpu']){
    const lines=visibleLines(w.get(n+'-pane'));
    assert.equal(new Set(lines).size,lines.length,JSON.stringify({n,state,link,lines}));
    assert.ok(lines.every(line=>!line.includes('not reported')&&!line.includes('not yet known')));
   }
   if(!link){
    assert.equal(w.get('gpu-speed').textContent,'GPU Link is off.');
    for(const id of ['gpu-model','gpu-state','gpu-answer','gpu-stop','gpu-disclosure'])assert.equal(w.get(id).hidden,true);
   }
  }
 }
 const s=snapshot(++rev);s.phone.model='';s.phone.where='';await w.show(s);
 assert.equal(w.get('phone-model').hidden,true);assert.equal(w.get('phone-state').hidden,true);
});

test('off link targets phone only and busy targeted panes disable asking but keep Stop',async()=>{
 for(const search of ['', '?pane=phone','?pane=gpu']){
  const w=world(search);let rev=0;
  for(const n of ['phone','gpu'])for(const state of ['queued','waiting','loading','answering','paused','done','failed','cancelled']){
   const s=snapshot(++rev);s[n].answer=answer(state);await w.show(s);
   const busy=['queued','waiting','loading','answering','paused'].includes(state);
   const targeted=!search||search==='?pane='+n;
   assert.equal(w.get('ask').disabled,busy&&targeted);
   for(const b of w.get('presets').children)assert.equal(b.disabled,busy&&targeted);
   assert.equal(w.get(n+'-stop').hidden,!busy);
   if(busy&&targeted){
    w.get('ask').onclick();w.get('presets').children[0].onclick();
    w.get('question').onkeydown({key:'Enter',preventDefault(){}});
    await w.tick();assert.equal(w.requests[0].options.method,'GET');await w.reply(s);
    assert.equal(w.get('err').textContent,'');
   }
  }
  const off=snapshot(++rev);off.gpu.link=null;off.gpu.answer=answer();await w.show(off);
  assert.equal(w.get('ask').textContent,search==='?pane=gpu'?'Ask the GPU':'Ask the phone model');
  assert.equal(w.get('ask').disabled,search==='?pane=gpu');
  w.get('ask').onclick();await w.tick();
  assert.equal(w.requests[0].options.method,search==='?pane=gpu'?'GET':'POST');
  if(search!=='?pane=gpu')assert.deepEqual(JSON.parse(w.requests[0].options.body).panes,['phone']);
 }
});

test('answer boxes follow growing text, preserve a reader above the end and reset for a new answer',async()=>{
 for(const n of ['phone','gpu']){
  const w=world(),s=snapshot();s[n].answer=answer();s[n].answer.text='x'.repeat(12000);await w.show(s);
  const box=w.get(n+'-answer');assert.equal(box.scrollTop,11900);
  s.rev++;s[n].answer.text+=' more';await w.show(s);assert.equal(box.scrollTop,11905);
  box.scrollTop=100;s.rev++;s[n].answer.text+=' more';await w.show(s);assert.equal(box.scrollTop,100);
  box.scrollTop=box.scrollHeight;s.rev++;s[n].answer.text+=' end';await w.show(s);assert.equal(box.scrollTop,box.scrollHeight-box.clientHeight);
  box.scrollTop=100;s.rev++;s[n].answer.id='new-answer';await w.show(s);
  assert.equal(box.scrollTop,box.scrollHeight-box.clientHeight,'a new answer starts at its end');
  box.scrollTop=100;
  s.rev++;s[n].answer.text='y'.repeat(12000);await w.show(s);
  assert.equal(box.scrollTop,100,'reader stays above the end');
 }
});

test('live pieces estimate is a headline only while answering without server speed',async()=>{
 const w=world(),s=snapshot();s.phone.answer=answer();s.phone.answer.tps=null;s.phone.answer.tokens=null;s.phone.live_speed=17;
 await w.show(s);assert.equal(w.get('phone-speed').textContent,'about 17 pieces per second');
 assert.equal(w.get('phone-state').textContent,'Answering');
 s.rev++;s.phone.answer.state='done';s.phone.live_speed=null;
 await w.show(s);assert.equal(w.get('phone-speed').textContent,'Done');
 s.rev++;s.phone.answer.state='answering';s.phone.answer.tps=18;s.phone.live_speed=17;
 await w.show(s);assert.equal(w.get('phone-speed').textContent,'18 tokens per second');
 s.rev++;s.phone.answer.state='done';s.phone.answer.tps=24.5;s.phone.answer.tokens=100;
 await w.show(s);assert.equal(w.get('phone-speed').textContent,'24.5 tokens per second');
 assert.equal(w.get('phone-state').textContent,'Done · 100 tokens');
});

test('requests time out even when fetch or its body ignores abort, releasing polling and Stop',async()=>{
 for(const stuckBody of [false,true]){
  const w=world(),s=snapshot();s.phone.answer=answer();s.phone.answer.tps=null;await w.show(s);
  await w.tick();const stalled=w.requests.shift();
  // The response helper can stand in for a fetch whose body never settles.
  if(stuckBody)stalled.resolveBody();
  await settle();
  w.get('phone-stop').onclick();
  await w.tick();assert.equal(stalled.options.signal.aborted,true);
  assert.equal(w.get('err').textContent,'The phone did not answer.');
  await w.tick();assert.equal(w.requests[0].at-stalled.at,10000);
  assert.deepEqual(JSON.parse(w.requests[0].options.body),{action:'cancel',id:s.phone.answer.id});
  s.rev++;s.phone.answer.state='cancelled';await w.reply(s);
  assert.equal(w.get('err').textContent,'');assert.equal(w.get('phone-speed').textContent,'Stopped');
  stalled.resolve({...s,rev:s.rev+10,phone:{...s.phone,model:'Late stale response'}});await settle();
  assert.notEqual(w.get('phone-model').textContent,'Late stale response');
  await w.tick();assert.equal(w.requests[0].options.method,'GET');await w.reply(s);
 }
});

test('cable line says when a PC is connected and what unplugging does',async()=>{
 const w=world();let rev=0,cable=()=>w.get('gpu-cable');
 let s=snapshot(++rev);await w.show(s);assert.equal(cable().hidden,true);assert.equal(cable().textContent,'');
 s=snapshot(++rev);s.gpu.link.pc=pc();await w.show(s);assert.equal(cable().hidden,false);
 assert.equal(cable().textContent,'Connected. Safe to unplug.');assert.equal(cable().dataset.cable,'ok');
 s=snapshot(++rev);s.gpu.link.pc=pc('copying');await w.show(s);
 assert.equal(cable().textContent,'Copying the model to the PC. Wait before unplugging.');assert.equal(cable().dataset.cable,'wait');
 for(const state of ['ready','loading','busy']){
  s=snapshot(++rev);s.gpu.link.pc=pc(state);s.gpu.answer=answer('answering');await w.show(s);
  assert.equal(cable().textContent,'Connected. Safe to unplug: the answer is saved and continues when the PC is ready again.',state);
  assert.equal(cable().dataset.cable,'ok');
 }
 for(const state of ['stopping','error']){
  s=snapshot(++rev);s.gpu.link.pc=pc(state);s.gpu.answer=answer('answering');await w.show(s);
  assert.equal(cable().textContent,'Connected. Safe to unplug: the answer is saved.',state);
  s=snapshot(++rev);s.gpu.link.pc=pc(state);await w.show(s);
  assert.equal(cable().textContent,'Connected. Safe to unplug.',state);
 }
 s=snapshot(++rev);s.gpu.link.pc=pc();s.gpu.link.pc.live=false;s.gpu.answer=answer('paused');await w.show(s);
 assert.equal(cable().textContent,'Not connected. The answer is saved and continues when the PC is ready again.');assert.equal(cable().dataset.cable,'out');
 s=snapshot(++rev);s.gpu.link.pc=pc('copying');s.gpu.link.pc.live=false;await w.show(s);
 assert.equal(cable().textContent,'Not connected. Check the cable and the PC.');assert.equal(cable().dataset.cable,'out');
 s=snapshot(++rev);s.gpu.link.pc=pc();s.gpu.answer=answer('done');await w.show(s);assert.equal(cable().textContent,'Connected. Safe to unplug.');
 s=snapshot(++rev);s.gpu.link=null;await w.show(s);assert.equal(cable().hidden,true);
 s=snapshot(++rev);s.gpu.link.pc=pc();await w.show(s);assert.equal(cable().hidden,false);
 await w.show({locked:true});assert.equal(cable().hidden,true);assert.equal(cable().textContent,'');
});

test('a link storage fault replaces GPU answering and waiting words until it clears',async()=>{
 const w=world();let rev=0;
 for(const state of ['answering','waiting',null])for(const speed of [null,42]){
  const s=snapshot(++rev);s.gpu.link.pc=pc();s.gpu.link.storage='error';
  s.gpu.answer=state?answer(state):null;
  if(state)s.gpu.answer.tps=speed;
  s.phone.answer=answer();s.phone.answer.tps=null;s.phone.link=s.gpu.link;
  await w.show(s);
  const id=state&&speed!==null?'gpu-state':'gpu-speed';
  assert.match(w.get(id).textContent,/^The phone could not save the answer\./);
  assert.equal(w.get('phone-speed').textContent,'Answering','the fault belongs to the GPU link');
  assert.equal(w.get('gpu-answer').textContent,state?s.gpu.answer.text:'');
  s.rev=++rev;s.gpu.link.storage=null;await w.show(s);
  assert.doesNotMatch(w.get(id).textContent,/could not save/);
 }
});

test('a link storage fault only asks to wait before unplugging a live PC',async()=>{
 const w=world();let rev=0;
 assert.equal(fixture.snapshot.gpu.link.storage,null,'the fake snapshot supplies storage');
 for(const state of ['ready','copying','stopping','error'])for(const live of [true,false])
 for(const answerState of [null,'answering','done']){
  const s=snapshot(++rev);s.gpu.link.pc={...pc(state),live};s.gpu.link.storage='error';
  s.gpu.answer=answerState?answer(answerState):null;await w.show(s);
  assert.equal(w.get('gpu-cable').dataset.cable,live?'wait':'out');
  assert.equal(w.get('gpu-cable').hidden,false);
  assert.equal(w.get('gpu-cable').textContent,'The phone could not save the answer.'+(live?' Wait before unplugging.':''));
  assert.doesNotMatch(w.get('gpu-cable').textContent,/answer is saved|Safe to unplug/);
  s.rev=++rev;s.gpu.link.storage=null;await w.show(s);
  assert.doesNotMatch(w.get('gpu-cable').textContent,/could not save/);
 }
 for(const enrol of ['open','closed'])for(const answerState of [null,'answering','done']){
  const s=snapshot(++rev);s.gpu.link.pc=null;s.gpu.link.enrol=enrol;s.gpu.link.storage='error';
  s.gpu.answer=answerState?answer(answerState):null;await w.show(s);
  assert.equal(w.get('gpu-cable').dataset.cable,'');
  assert.equal(w.get('gpu-cable').hidden,false);
  assert.equal(w.get('gpu-cable').textContent,'The phone could not save the answer.');
 }
});

test('live lines are written only when their words change',async()=>{
 const w=world();let rev=0;
 let s=snapshot(++rev);s.gpu.link.pc=pc('busy');s.gpu.answer=answer('answering');s.phone.answer=answer('answering');s.phone.answer.tps=null;
 await w.show(s);
 const ids=['gpu-cable','gpu-speed','phone-speed'],before=ids.map(id=>w.get(id).writes);
 assert.ok(before.every(n=>n>=1));
 for(let i=0;i<5;i++){s=JSON.parse(JSON.stringify(s));s.rev=++rev;await w.show(s)}
 assert.deepEqual(ids.map(id=>w.get(id).writes),before);
});

test('an unknown or inherited error name never becomes a headline',async()=>{
 const w=world();let rev=0;
 for(const code of ['constructor','__proto__','toString','nonsense']){
  const s=snapshot(++rev);s.gpu.answer=answer('failed');s.gpu.answer.error=code;s.gpu.answer.tps=null;await w.show(s);
  assert.equal(w.get('gpu-speed').textContent,'The PC could not finish the answer.',code);
 }
});

test('an old answer keeps the PC it came from; a queued one shows the current PC',async()=>{
 const w=world();let rev=0;
 let s=snapshot(++rev);s.gpu.answer=answer('done');s.gpu.answer.where='PC A';s.gpu.where='PC A';s.gpu.link.pc={...pc(),label:'PC B',gpu:{name:'B GPU'}};
 await w.show(s);assert.equal(w.get('gpu-model').textContent,'Large model · PC A');
 s=snapshot(++rev);s.gpu.answer=answer('done');s.gpu.answer.where='Demo PC';s.gpu.link.pc=pc();await w.show(s);
 assert.equal(w.get('gpu-model').textContent,'Large model · Demo PC · Demo GPU');
 s=snapshot(++rev);s.gpu.answer=answer('waiting');s.gpu.answer.where='';s.gpu.link.pc={...pc(),label:'PC B',gpu:{name:'B GPU'}};await w.show(s);
 assert.equal(w.get('gpu-model').textContent,'Large model · PC B · B GPU');
});
test('PC pane never uses phone wording for an error code the PC sent',async()=>{
 const w=world();let rev=0;
 for(const code of ['no_model','stream_error','storage_error','restart','worker_start']){
  const s=snapshot(++rev);s.gpu.link.pc=pc();s.gpu.answer=answer('failed');s.gpu.answer.error=code;s.gpu.answer.tps=null;await w.show(s);
  assert.equal(w.get('gpu-speed').textContent,'The PC could not finish the answer.',code);
 }
});
test('dead cable with a stopping or failed PC does not promise a continuation',async()=>{
 const w=world();let rev=0;
 for(const state of ['stopping','error']){
  const s=snapshot(++rev);s.gpu.link.pc={...pc(state),live:false};s.gpu.answer=answer('paused');await w.show(s);
  assert.doesNotMatch(w.get('gpu-cable').textContent,/continues/,state);
 }
});
test('a copy with zero total bytes shows no NaN',async()=>{
 const w=world();const s=snapshot(1);s.gpu.link.pc={...pc('copying'),copied:0,total:0};await w.show(s);
 assert.ok(!/NaN/.test(w.get('gpu-speed').textContent));
});
test('a waiting answer shows copy progress, not a bare wait',async()=>{
 const w=world();const s=snapshot(1);s.gpu.answer=answer('waiting');s.gpu.answer.tps=null;s.gpu.link.pc=pc('copying');await w.show(s);
 assert.equal(w.get('gpu-speed').textContent,'Copying the model: 41%');
});

test('the headline follows the PC state when no answer is shown',async()=>{
 const w=world();let rev=0;
 for(const [state,expected] of [['idle','The PC is not ready yet.'],['busy','The PC is busy.'],['ready','Ready'],
  ['loading','Loading the model'],['stopping','Stopping'],['error','The PC could not run the model.']]){
  const s=snapshot(++rev);s.gpu.link.pc=pc(state);await w.show(s);
  assert.equal(w.get('gpu-speed').textContent,expected,state);
 }
 const s=snapshot(++rev);s.gpu.link.pc=pc('idle');s.gpu.answer=answer('waiting');s.gpu.answer.tps=null;await w.show(s);
 assert.equal(w.get('gpu-speed').textContent,'The PC is not ready yet.');
});

test('a finished answer without a recorded PC is never credited to the PC that is enrolled now',async()=>{
 const w=world();let rev=0;
 let s=snapshot(++rev);s.gpu.answer=answer('done');s.gpu.answer.where='';s.gpu.where="on the PC's GPU";
 s.gpu.link.pc={...pc(),label:'PC B',gpu:{name:'B GPU'}};await w.show(s);
 assert.equal(w.get('gpu-model').textContent,"Large model · on the PC's GPU");
});
