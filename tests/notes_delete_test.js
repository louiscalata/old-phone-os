// SPDX-License-Identifier: GPL-3.0-or-later
// Execute the actual Notes script with deferred local requests, no browser/server.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../src/opos/notes_page.py'), 'utf8').split('_SCRIPT = r"""')[1].split('"""')[0];
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
async function world() {
  const nodes = {};
  function element() { return {value:'', textContent:'', hidden:false, disabled:false, dataset:{}, children:[], appendChild(n){this.children.push(n);}}; }
  for (const id of ['saved','rows','body','fresh','del']) nodes[id] = element();
  const requests = [], timers = new Map(); let nextTimer = 0;
  const context = vm.createContext({document:{getElementById:id=>nodes[id], createElement:element, querySelector:()=>({content:'fixture-token'})},
    fetch:(_url, options) => new Promise((resolve,reject)=>{
      const body = options.body ? JSON.parse(options.body) : null;
      if (!body) resolve({ok:true,json:()=>Promise.resolve({notes:[]})});
      else requests.push({body, resolve:(data,ok=true)=>resolve({ok,json:()=>Promise.resolve(data)}),reject});
    }), setTimeout:fn=>{const id=++nextTimer;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(source, context); await settle();
  function take(action) { const at = requests.findIndex(r=>r.body.action===action); assert.notEqual(at,-1,'request '+action);return requests.splice(at,1)[0]; }
  async function open(id,text) { context.open(id); take('open').resolve({notes:[{id,title:text}],id,text}); await settle(); }
  async function remove() {nodes.del.onclick();nodes.del.onclick();await settle();}
  function type(text) {nodes.body.value=text;nodes.body.oninput();}
  async function tick() {const pending=[...timers.values()];timers.clear();for(const fn of pending)fn();await settle();}
  return {nodes,requests,open,remove,type,tick,take,context};
}
test('failed deletion keeps the selected note and its text', async()=>{
  const w=await world();await w.open('aaaaaaaa','Original');await w.remove();
  w.take('delete').resolve({error:'internal error'},false);await settle();
  assert.equal(w.context.cur,'aaaaaaaa');assert.equal(w.nodes.body.value,'Original');
  assert.match(w.nodes.saved.textContent,/error|could not/i);assert.equal(w.nodes.body.disabled,false);
});
test('deleting A does not clear B or redirect its pending save', async()=>{
  const w=await world();await w.open('aaaaaaaa','A');await w.remove();const deletion=w.take('delete');
  await w.open('bbbbbbbb','B');w.type('B draft');
  deletion.resolve({ok:true,notes:[{id:'bbbbbbbb',title:'B'}]});await settle();
  assert.equal(w.context.cur,'bbbbbbbb');assert.equal(w.nodes.body.value,'B draft');
  await w.tick();assert.deepEqual(w.take('save').body,{action:'save',id:'bbbbbbbb',text:'B draft'});
});
test('pending save for A completes before its delete is sent', async()=>{
  const w=await world();await w.open('aaaaaaaa','A');w.type('A draft');await w.remove();
  assert.equal(w.requests.some(r=>r.body.action==='delete'),false);
  const save=w.take('save');assert.equal(save.body.text,'A draft');save.resolve({notes:[],saved:'Saved'});await settle();
  w.take('delete').resolve({ok:true,notes:[]});await settle();
  assert.equal(w.context.cur,null);assert.equal(w.nodes.body.value,'');assert.equal(w.nodes.del.hidden,true);
});
test('an in-flight save failure preserves the draft and prevents deletion', async()=>{
  const w=await world();await w.open('aaaaaaaa','A');w.type('A draft');await w.tick();const save=w.take('save');
  await w.remove();save.resolve({error:'disk error'},false);await settle();
  assert.equal(w.requests.some(r=>r.body.action==='delete'),false);
  assert.equal(w.context.cur,'aaaaaaaa');assert.equal(w.nodes.body.value,'A draft');
  assert.match(w.nodes.saved.textContent,/error|could not/i);
});
test('network rejection is reported without clearing the editor', async()=>{
  const w=await world();await w.open('aaaaaaaa','Retain me');await w.remove();
  w.take('delete').reject(new Error('connection lost'));await settle();
  assert.equal(w.context.cur,'aaaaaaaa');assert.equal(w.nodes.body.value,'Retain me');
  assert.equal(w.nodes.saved.textContent,'connection lost');assert.equal(w.nodes.body.disabled,false);
});
test('deletion also waits for an already in-flight successful save', async()=>{
  const w=await world();await w.open('aaaaaaaa','A');w.type('A saved');await w.tick();const save=w.take('save');
  await w.remove();assert.equal(w.requests.some(r=>r.body.action==='delete'),false);
  save.resolve({notes:[],saved:'Saved'});await settle();
  w.take('delete').resolve({ok:true,notes:[]});await settle();
  assert.equal(w.context.cur,null);assert.equal(w.nodes.body.value,'');assert.equal(w.nodes.body.disabled,false);
});
test('a delayed open cannot replace a draft while its save is pending', async()=>{
  const w=await world();await w.open('aaaaaaaa','A');
  w.context.open('bbbbbbbb');const opening=w.take('open');w.type('B draft');
  opening.resolve({notes:[{id:'bbbbbbbb',title:'B original'}],id:'bbbbbbbb',text:'B original'});await settle();
  assert.equal(w.context.cur,'bbbbbbbb');assert.equal(w.nodes.body.value,'B draft');
  await w.tick();assert.deepEqual(w.take('save').body,{action:'save',id:'bbbbbbbb',text:'B draft'});
});
test('a delayed open cannot replace a draft after its save completes', async()=>{
  const w=await world();await w.open('aaaaaaaa','A');
  w.context.open('bbbbbbbb');const opening=w.take('open');w.type('B draft');await w.tick();
  const save=w.take('save');assert.deepEqual(save.body,{action:'save',id:'bbbbbbbb',text:'B draft'});
  save.resolve({notes:[{id:'bbbbbbbb',title:'B draft'}],id:'bbbbbbbb',saved:'Saved fixture'});await settle();
  assert.equal(w.context.pending.bbbbbbbb,undefined);assert.equal(w.context.writing.bbbbbbbb,undefined);
  opening.resolve({notes:[{id:'bbbbbbbb',title:'B original'}],id:'bbbbbbbb',text:'B original'});await settle();
  assert.equal(w.context.cur,'bbbbbbbb');assert.equal(w.nodes.body.value,'B draft');
  assert.equal(w.nodes.saved.textContent,'Saved fixture');assert.equal(w.requests.length,0);
});
