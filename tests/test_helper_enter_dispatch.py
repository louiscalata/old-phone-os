# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise both production Enter listeners with the real legacy Ask callback."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The browser boundary is replaced; production listeners, helperCall and the
# complete enhancement run unchanged and in their served registration order.
HARNESS = r"""
const vm=require('vm'),fs=require('fs'),root=process.argv[1];
const read=n=>fs.readFileSync(root+'/src/opos/web_static/'+n,'utf8');
const page=fs.readFileSync(root+'/src/opos/web.py','utf8');
const start=page.indexOf('let helperCtl=null;'),end=page.indexOf('\nfunction helperReset()',start);
if(start<0||end<start)throw new Error('production helperCall boundary missing');
const legacyCall=page.slice(start,end);
function world(opt={}){
 const ids={},all=[],posts=[],timers=[];
 function el(id='',tag='div'){
  const listeners={};
  const e={id,tagName:tag.toUpperCase(),dataset:{},attrs:{},hidden:false,disabled:false,
   value:'',checked:false,children:[],parentNode:null,_text:'',clicks:0,
   get textContent(){return this._text+this.children.map(x=>x.textContent).join(' ')},
   set textContent(v){for(const c of this.children)c.parentNode=null;this.children=[];this._text=String(v)},
   get firstChild(){return this.children[0]||null},
   get nextSibling(){if(!this.parentNode)return null;const a=this.parentNode.children;return a[a.indexOf(this)+1]||null},
   appendChild(n){return this.insertBefore(n,null)},
   insertBefore(n,ref){if(n.parentNode)n.parentNode.removeChild(n);const i=ref?this.children.indexOf(ref):-1;
    if(i<0)this.children.push(n);else this.children.splice(i,0,n);n.parentNode=this;return n},
   removeChild(n){const i=this.children.indexOf(n);if(i>=0)this.children.splice(i,1);n.parentNode=null;return n},
   setAttribute(k,v){this.attrs[k]=String(v)},getAttribute(k){return this.attrs[k]??null},
   removeAttribute(k){delete this.attrs[k]},focus(){},scrollIntoView(){},querySelector(){return null},closest(){return null},
   addEventListener(t,f){(listeners[t]??=[]).push(f)},
   fire(type,init={}){const ev={...init,target:this,currentTarget:this,defaultPrevented:!!init.defaultPrevented,
    preventDefault(){this.defaultPrevented=true}};
    if(typeof this['on'+type]==='function')this['on'+type](ev);
    for(const f of listeners[type]||[])f(ev);return ev},
   click(){if(!this.disabled){this.clicks++;return this.fire('click')}}};
  all.push(e);return e;
 }
 const doc={documentElement:el('html'),createElement(tag){return el('',tag)},createTextNode(t){if(opt.failSources)throw new Error('render failed');const n=el('','#text');n._text=String(t);return n},
  getElementById(id){return ids[id]||all.find(e=>e.id===id&&e.parentNode)||null},
  querySelector(s){return s==='meta[name=opos-token]'||s==='[name=opos-token]'?{content:'synthetic-test-token'}:null},querySelectorAll(){return []}};
 for(const id of ['phone','home','back','settings','ask','q','askbtn','planbtn','answer','webq','why','demo-phone-note',
  'meter','meter-hit','sheet','sheet-title','scrim','sheet-handle','time-left','sheet-grip','demo','battery','battery-val',
  'simbtn','visbtn','helpertoggle','temp','charge','goal','goal-clear','number','msg','screen'])ids[id]=el(id);
 ids.phone.appendChild(ids.ask);ids.ask.appendChild(ids.q);ids.ask.appendChild(ids.askbtn);ids.ask.appendChild(ids.answer);
 if(opt.unavailable)ids.phone.appendChild(ids.q); // Enhancement cannot claim a non-direct input.
 const ctx={document:doc,location:{search:''},URLSearchParams,Promise,AbortController,JSON,String,Date,performance:{now:()=>Date.now()},
  localStorage:{getItem(){return null},setItem(){},removeItem(){}},matchMedia(){return{matches:false}},
  addEventListener(){},scrollTo(){},render:async()=>{},api:async()=>({}),go(){},toggleSim(){},
  setTimeout(f,ms){const t={f,ms,live:true};timers.push(t);return t},clearTimeout(t){if(t)t.live=false},
  setInterval(f,ms){const t={f,ms,live:true};timers.push(t);return t},clearInterval(t){if(t)t.live=false},
  fetch(url,o){if(url==='/api/state'){
   if(o?.headers?.['X-OPOS-Token']!=='synthetic-test-token')throw new Error('missing state token');
   return Promise.resolve({ok:true,json:async()=>({})});}
   const rec={body:JSON.parse(o.body),aborted:false};posts.push(rec);
   return new Promise((resolve,reject)=>{rec.reply=j=>resolve({ok:true,status:200,json:async()=>j});
    o.signal.addEventListener('abort',()=>{rec.aborted=true;const e=new Error('aborted');e.name='AbortError';reject(e)})})}};
 ctx.window=ctx;vm.createContext(ctx);
 for(const file of ['js-1.js','js-2.js'])vm.runInContext(read(file),ctx,{filename:file});
 vm.runInContext(legacyCall,ctx,{filename:'web.py:helperCall'});
 vm.runInContext(read('js-8.js'),ctx,{filename:'js-8.js'});
 vm.runInContext(read('phone-b.js'),ctx,{filename:'phone-b.js'});
 const flush=async()=>{for(let i=0;i<40;i++)await null};
 const open=async()=>{ctx.oposUI.openApp('helper');await flush()};
 const key=async(init={})=>{const ev=ids.q.fire('keydown',{key:'Enter',...init});await flush();return ev.defaultPrevented};
 const send=async()=>{doc.getElementById('hs-send').click();await flush()};
 const state=()=>({count:posts.length,asks:posts.map(r=>r.body),aborted:posts.map(r=>r.aborted),
  legacyClicks:ids.askbtn.clicks,legacyWaiting:!!doc.getElementById('helperwait'),marker:ids.ask.dataset.hs||'',
  question:ids.q.value,status:doc.getElementById('hs-status')?.textContent||''});
 return{ids,doc,posts,flush,open,key,send,state};
}
(async()=>{
 const out={};
 for(const kind of ['normal','visibleSend','composing','repeat','prevented','inFlight','fallback','failedEnhancement','settledRepeat','empty','otherKey']){
  const w=world({unavailable:kind==='fallback',failSources:kind==='failedEnhancement'});await w.open();w.ids.q.value='Synthetic question';
  if(kind==='visibleSend')await w.send();
  else if(kind==='inFlight'){await w.send();out.inFlightBefore=w.state();await w.key()}
  else if(kind==='settledRepeat'){
   await w.send();w.posts[0].reply({ok:true,text:'Answer'});await w.flush();out.settledBefore=w.state();
   await w.key({repeat:true});out.settledAfterRepeat=w.state();w.ids.q.value='A new question';await w.key();
  }
  else if(kind==='empty'){w.ids.q.value='  ';await w.key()}
  else if(kind==='otherKey')await w.key({key:'a'});
  else if(kind==='failedEnhancement'){
   await w.send();w.posts[0].reply({ok:true,text:'Reply',sources:[{title:'Source',url:'https://en.wikipedia.org/wiki/Battery'}]});
   await w.flush();out.failedBefore=w.state();await w.key();
  }else out[kind+'Prevented']=await w.key({isComposing:kind==='composing',repeat:kind==='repeat',defaultPrevented:kind==='prevented'});
  out[kind]=w.state();
  if(kind==='normal'){w.doc.getElementById('hs-cancel').click();await w.flush();out.afterCancel=w.state()}
 }
 console.log(JSON.stringify(out));
})().catch(e=>{console.error(e.stack);process.exitCode=1});
"""


@unittest.skipUnless(shutil.which("node"), "node unavailable")
class HelperEnterDispatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [shutil.which("node"), "-e", HARNESS, str(ROOT)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        cls.results = json.loads(result.stdout)

    def test_enter_dispatches_one_request_without_hidden_legacy_progress(self):
        r = self.results["normal"]
        self.assertEqual(r["count"], 1, r)
        self.assertEqual(r["legacyClicks"], 0, r)
        self.assertFalse(r["legacyWaiting"], r)
        self.assertEqual(r["asks"], [{"action": "ask", "text": "Synthetic question", "web": False}])
        self.assertTrue(self.results["normalPrevented"])

    def test_visible_send_dispatches_one_request(self):
        r = self.results["visibleSend"]
        self.assertEqual(r["count"], 1, r)
        self.assertEqual(r["legacyClicks"], 0, r)

    def test_composing_repeated_and_handled_enter_do_not_dispatch(self):
        for kind in ("composing", "repeat", "prevented"):
            with self.subTest(kind=kind):
                self.assertEqual(self.results[kind]["count"], 0, self.results[kind])
        self.assertFalse(self.results["composingPrevented"])
        self.assertFalse(self.results["repeatPrevented"])

    def test_in_flight_enter_does_not_start_another_request(self):
        self.assertEqual(self.results["inFlightBefore"]["count"], 1)
        self.assertEqual(self.results["inFlight"]["count"], 1, self.results["inFlight"])

    def test_cancel_aborts_the_only_request(self):
        r = self.results["afterCancel"]
        self.assertEqual(r["aborted"], [True], r)
        self.assertEqual(r["status"], "Cancelled.")
        self.assertFalse(r["legacyWaiting"], r)

    def test_unavailable_enhancement_keeps_legacy_enter(self):
        r = self.results["fallback"]
        self.assertEqual((r["count"], r["legacyClicks"], r["marker"]), (1, 1, ""), r)
        self.assertTrue(r["legacyWaiting"], r)

    def test_failed_enhancement_releases_enter_to_legacy(self):
        before = self.results["failedBefore"]
        self.assertEqual((before["count"], before["marker"]), (1, ""), before)
        r = self.results["failedEnhancement"]
        self.assertEqual((r["count"], r["legacyClicks"], r["marker"]), (2, 1, ""), r)
        self.assertTrue(r["legacyWaiting"], r)

    def test_repeat_after_a_reply_does_not_create_a_new_turn(self):
        self.assertEqual(self.results["settledBefore"]["count"], 1)
        self.assertEqual(self.results["settledBefore"]["status"], "Answered.")
        self.assertEqual(self.results["settledBefore"]["question"], "")
        self.assertEqual(self.results["settledAfterRepeat"]["count"], 1)
        self.assertEqual(self.results["settledRepeat"]["count"], 2)
        self.assertEqual(self.results["settledRepeat"]["legacyClicks"], 0)

    def test_empty_enter_and_other_keys_do_not_dispatch(self):
        for kind in ("empty", "otherKey"):
            with self.subTest(kind=kind):
                self.assertEqual(self.results[kind]["count"], 0, self.results[kind])
