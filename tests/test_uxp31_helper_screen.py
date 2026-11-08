# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from opos.web import page
from opos import assist

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"
STD = "The helper did not reply."
REST = "The helper is resting to save power. Plug the phone in, then ask again."
WHY = "Battery is low, so the helper keeps calls first."

# Fake DOM + fake fetch so phone-b.js runs under node. Each scenario gets a fresh context; fetch behaviour is a queue.
HARNESS = r"""
const vm=require('vm'),fs=require('fs'),SRC=fs.readFileSync(process.argv[1],'utf8'),WHY=process.argv[2];
function world(opt){opt=opt||{};
 const ids={},all=[];
 function el(id,tag){const L={};const e={id,tagName:(tag||'div').toUpperCase(),hidden:false,disabled:false,dataset:{},attrs:{},value:'',children:[],parentNode:null,_t:'',focused:0,
  get textContent(){return [this._t].concat(this.children.map(c=>c.textContent)).filter(Boolean).join(' ')},set textContent(v){this.children=[];this._t=String(v)},
  get firstChild(){return this.children[0]||null},
  appendChild(n){return this.insertBefore(n,null)},
  insertBefore(n,ref){if(n.parentNode)n.parentNode.removeChild(n);const i=ref?this.children.indexOf(ref):-1;if(i<0)this.children.push(n);else this.children.splice(i,0,n);n.parentNode=this;return n},
  removeChild(n){const i=this.children.indexOf(n);if(i>=0)this.children.splice(i,1);n.parentNode=null;return n},
  setAttribute(k,v){this.attrs[k]=String(v)},getAttribute(k){return k in this.attrs?this.attrs[k]:null},
  focus(){this.focused++},scrollIntoView(){},querySelector(){return null},closest(){return null},
  addEventListener(t,f){(L[t]=L[t]||[]).push(f)},
  fire(t,ev){ev=ev||{};ev.target=ev.target||this;ev.preventDefault=ev.preventDefault||function(){};(L[t]||[]).forEach(f=>f(ev))}};
  all.push(e);return e}
 const doc={documentElement:el('html'),createElement(t){return el('',t)},querySelector(s){return /opos-token/.test(s)?{content:'tok'}:null},
  getElementById(i){return ids[i]||all.find(e=>e.id===i&&e.parentNode)||null}};
 ['phone','home','back','settings','ask','q','askbtn','answer','webq','why','demo-phone-note'].forEach(i=>ids[i]=el(i));
 ids.ask.hidden=true;ids.ask.appendChild(ids.q);ids.ask.appendChild(ids.answer);ids.phone.appendChild(ids.ask);ids.why.textContent=WHY;
 const out={toggles:0,fetches:[]},timers=[],intervals=[],queue=[];let now=0;
 const ctx={document:doc,location:{search:''},localStorage:{getItem(){return null},setItem(){},removeItem(){}},matchMedia(){return{matches:false}},
  addEventListener(){},scrollTo(){},Promise,AbortController:opt.noAbort?undefined:AbortController,JSON,String,Date:{now:()=>now},vis:false,
  setTimeout(f,ms){if(ms>=15000){const t={f,ms,at:now+ms,live:true};timers.push(t);return t}return setTimeout(f,ms)},
  clearTimeout(t){if(t&&t.f)t.live=false;else clearTimeout(t)},
  setInterval(f,ms){const t={f,ms,live:true};intervals.push(t);return t},clearInterval(t){if(t)t.live=false},
  toggleVis(){out.toggles++;ctx.vis=true;ids.ask.hidden=false;return Promise.resolve()},
  fetch(url,o){if(opt.throwFetch)throw new Error('sync');const rec={url,body:JSON.parse(o.body),aborted:false};out.fetches.push(rec);
   o.signal.addEventListener('abort',()=>{rec.aborted=true});const how=queue.shift()||'hang';
   if(how==='late')return new Promise(resolve=>{rec.reply=j=>resolve({ok:true,status:200,json:()=>Promise.resolve(j)})});
   if(how==='bodyhang')return Promise.resolve({ok:true,status:200,json:()=>new Promise(()=>{})});
   if(how==='hang')return new Promise((_,rej)=>o.signal.addEventListener('abort',()=>{const e=new Error('aborted');e.name='AbortError';rej(e)}));
   if(how==='hidden'||how==='error')return Promise.resolve({ok:true,json:()=>Promise.resolve({error:how==='hidden'?'helper hidden':'Try again later.'})});
   if(how==='net')return Promise.reject(new TypeError('Failed to fetch'));
   if(['403','429','500'].includes(how))return Promise.resolve({ok:false,status:Number(how),json:()=>Promise.resolve({error:'boom'})});
   if(how==='empty')return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve({ok:true,text:'  ',engine:'Muse Glimmer',where:'on this device'})});
   if(how==='rest')return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve(JSON.parse(process.argv[3]))});
   if(how==='okfalse')return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve({ok:false,text:'There is no answer right now.',engine:'none',where:''})});
   if(how==='src')return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve({ok:true,text:'Wiki says hi',engine:'rules',where:'on this phone',
    sources:[{title:'Battery',url:'https://en.wikipedia.org/wiki/Battery'},{title:'Bad',url:'javascript:alert(1)'}]})});
   return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve({ok:true,text:'A:'+rec.body.text,engine:'rules',where:'on this phone'})})}};
 ctx.window=ctx;vm.createContext(ctx);vm.runInContext(SRC,ctx);
 const $=i=>doc.getElementById(i);
 const flush=async()=>{for(let i=0;i<30;i++)await null;await new Promise(r=>setTimeout(r,5))};
 const hooks=simulation=>(ctx.oposHooks||[]).forEach(f=>f({helper:{engine:'Llama 3.2 1B, on this phone'},simulation:{modem:simulation}}));
 const log=()=>{const l=$('hs-log');return l?l.children.map(li=>li.textContent):null};
 const ui={state(){const s=$('hs-send'),c=$('hs-cancel'),r=$('hs-retry'),st=$('hs-status'),l=$('hs-log');
  return{app:ids.phone.dataset.app||'',sendDisabled:s?s.disabled:null,cancelDisabled:c?c.disabled:null,retryHidden:r?r.hidden:null,
   status:st?st.textContent:null,live:st?st.getAttribute('aria-live'):null,logRole:l?l.getAttribute('role'):null,logLive:l?l.getAttribute('aria-live'):null,barHidden:$('hs-bar')?$('hs-bar').hidden:null,log:log(),logHidden:l?l.hidden:null,
   toggles:out.toggles,fetches:out.fetches.length,aborted:out.fetches.map(f=>f.aborted),texts:out.fetches.map(f=>f.body.text),
   actions:out.fetches.map(f=>f.body.action),timers:timers.length,liveTimers:timers.filter(t=>t.live).length,liveIntervals:intervals.filter(t=>t.live).length,askHs:ids.ask.dataset.hs||'',demoNote:ids['demo-phone-note'].textContent}}};
 const open=async n=>{ctx.oposUI.openApp(n);await flush();hooks();await flush()};
 const ask=async(text,enter)=>{ids.q.value=text;if(enter)ids.q.fire('keydown',{key:'Enter'});else $('hs-send').fire('click');await flush()};
 const advance=async ms=>{now+=ms;timers.filter(t=>t.live&&t.at<=now).forEach(t=>{t.live=false;t.f()});intervals.filter(t=>t.live).forEach(t=>t.f());await flush()};
 const reply=async(i,text)=>{out.fetches[i].reply(typeof text==='string'?{ok:true,text,engine:'Muse Glimmer',where:'on this device'}:text);await flush()};
 return{ids,ctx,$,flush,hooks,ui,open,ask,queue,timers,advance,reply};
}
const S={
 async restresponse(){const w=world();await w.open('helper');w.queue.push('rest');await w.ask('what is paused?');const rested=w.ui.state();
  await w.ask('again',true);const repeated=w.ui.state();w.$('hs-clear').fire('click');await w.flush();const cleared=w.ui.state();(w.ctx.oposHooks||[]).forEach(f=>f({helper:{mode:'SAVER'}}));await w.flush();
  const recovered=w.ui.state();w.queue.push('ok');await w.ask('after charging');return{rested,repeated,cleared,recovered,after:w.ui.state()}},
 async restrecoveryrace(){const states={};for(const mode of ['NORMAL','SAVER','CRITICAL']){const w=world();await w.open('helper');w.queue.push('late');await w.ask('earlier request');
  (w.ctx.oposHooks||[]).forEach(f=>f({helper:{mode}}));await w.flush();await w.reply(0,JSON.parse(process.argv[3]));states[mode]=w.ui.state()}return states},
 async reststate(){const w=world();(w.ctx.oposHooks||[]).forEach(f=>f({helper:{mode:'CRITICAL'}}));await w.open('helper');const opened=w.ui.state();
  await w.ask('not sent',true);const blocked=w.ui.state();(w.ctx.oposHooks||[]).forEach(f=>f({helper:{mode:'NORMAL'}}));await w.flush();
  const recovered=w.ui.state();w.queue.push('ok');await w.ask('recovered');return{opened,blocked,recovered,after:w.ui.state()}},
 async goalcritical(){const out={};for(const u of [36,9]){const w=world();(w.ctx.oposHooks||[]).forEach(f=>f({helper:{mode:'CRITICAL',usable_percent:u}}));await w.open('helper');out[u]=w.ui.state()}return out},
 async restduringwait(){const w=world();await w.open('helper');w.queue.push('late');await w.ask('already sent');
  (w.ctx.oposHooks||[]).forEach(f=>f({helper:{mode:'CRITICAL'}}));await w.flush();const waiting=w.ui.state();
  w.$('hs-cancel').fire('click');await w.flush();const cancelled=w.ui.state();await w.reply(0,'Stale answer');return{waiting,cancelled,after:w.ui.state()}},

 async backends(){const w=world();const absent=w.ui.state().demoNote;await w.open('helper');w.hooks(true);const simulated=w.ui.state().demoNote,helper=w.$('helper-where').textContent;w.hooks(false);const external=w.ui.state().demoNote;w.hooks();return{absent,simulated,helper,external,unknown:w.ui.state().demoNote}},
 async errors(){const w=world();w.hooks();await w.open('helper');w.queue.push('hidden','error');await w.ask('one');const hidden=w.ui.state();await w.ask('two');return{hidden,other:w.ui.state()}},
 async fallback(){const w=world();let payload;const reply=await w.ctx.helperAsk('draft',true,o=>{payload=o;return Promise.resolve({error:'helper hidden'})});
  const cancelled=await w.ctx.helperAsk('draft',false,()=>Promise.resolve(null));return{payload,reply,cancelled}},
 async lazy(){const w=world();w.hooks();await w.flush();const before=w.ui.state();before.hsExists=!!w.$('hs-send');
  await w.open('helper');const first=w.ui.state();await w.open('home');await w.open('helper');const again=w.ui.state();
  return{before,first,again}},
 async cancel(){const w=world();w.hooks();await w.open('helper');await w.ask('slow one');const waiting=w.ui.state();
  w.$('back').fire('click');await w.flush();const navWhileWaiting=w.ui.state().app;await w.open('helper');
  w.$('hs-cancel').fire('click');await w.flush();return{waiting,navWhileWaiting,after:w.ui.state()}},
 async retry(){const w=world();w.hooks();await w.open('helper');w.queue.push('500','ok');await w.ask('hello');const failed=w.ui.state();
  w.ids.q.value='something else';w.$('hs-retry').fire('click');await w.flush();return{failed,after:w.ui.state()}},
 async timeout(){const w=world();w.hooks();await w.open('helper');await w.ask('slow');const t=w.timers.filter(x=>x.live);
  t.forEach(x=>x.f());await w.flush();return{timers:t.length,after:w.ui.state()}},
 async slowreply(){const w=world();w.hooks();await w.open('helper');w.queue.push('late');await w.ask('long question');
  await w.advance(15000);const at15=w.ui.state();await w.advance(65000);const at80=w.ui.state();await w.advance(40000);const at120=w.ui.state();await w.reply(0,'Real delayed answer');return{at15,at80,at120,after:w.ui.state()}},
 async stuckbody(){const w=world();w.hooks();await w.open('helper');w.queue.push('bodyhang');await w.ask('stalled body');
  const deadline=w.timers.find(t=>t.live).ms;await w.advance(deadline);return{deadline,after:w.ui.state()}},
 async latecancel(){const w=world();w.hooks();await w.open('helper');w.queue.push('late','ok');await w.ask('first');
  w.$('hs-send').fire('click');await w.flush();const inFlight=w.ui.state();w.$('hs-cancel').fire('click');await w.flush();const cancelled=w.ui.state();
  w.$('hs-retry').fire('click');await w.flush();const retried=w.ui.state();await w.reply(0,'Stale answer');return{inFlight,cancelled,retried,after:w.ui.state()}},
 async httpempty(){const w=world();w.hooks();await w.open('helper');const states={};for(const kind of ['403','429','500','empty']){w.queue.push(kind);await w.ask(kind);states[kind]=w.ui.state()}return states},
 async bound(){const w=world();w.hooks();await w.open('helper');for(let i=1;i<=7;i++){w.queue.push('ok');await w.ask('q'+i,i%2===0)}
  const full=w.ui.state();w.$('hs-clear').fire('click');await w.flush();return{full,cleared:w.ui.state()}},
 async announce(){const w=world();w.hooks();await w.open('helper');w.queue.push('ok','ok');await w.ask('one');const li=w.$('hs-log').children[0];
  await w.ask('two');return{st:w.ui.state(),sameRow:w.$('hs-log').children[0]===li}},
 async sources(){const w=world();w.hooks();await w.open('helper');w.queue.push('src');await w.ask('wiki');
  const ap=w.$('hs-log').children[0].children[1];
  return{log:w.ui.state().log,links:ap.children.filter(c=>c.tagName==='A').map(a=>({t:a.textContent,href:a.href||'',rel:a.rel}))}},
 async noabort(){const w=world({noAbort:true});w.hooks();await w.open('helper');return{st:w.ui.state(),hs:!!w.$('hs-send')}},
 async throwfetch(){const w=world({throwFetch:true});w.hooks();await w.open('helper');await w.ask('x');const st=w.ui.state();
  w.ids.q.value='y';w.ids.q.fire('keydown',{key:'Enter'});await w.flush();const again=w.ui.state();
  w.$('back').fire('click');await w.flush();return{st,again,app:w.ui.state().app}},
 async renderfail(){const w=world();w.hooks();await w.open('helper');const create=w.ctx.document.createElement;
  w.ctx.document.createElement=function(t){if(t==='a')throw new Error('render fails');return create(t)};
  w.queue.push('src');await w.ask('wiki');const st=w.ui.state();
  let prevented=false;w.ids.q.fire('keydown',{key:'Enter',preventDefault(){prevented=true}});await w.flush();
  w.$('back').fire('click');await w.flush();const home=w.ui.state().app;await w.open('helper');
  return{st,prevented,home,again:w.ui.state(),liveTimers:w.timers.filter(t=>t.live).length}},
 async failure(){const w=world();w.hooks();await w.open('helper');w.queue.push('net','okfalse');await w.ask('x');const net=w.ui.state();
  await w.ask('y');const okfalse=w.ui.state();w.$('back').fire('click');await w.flush();const afterBack=w.ui.state().app;
  w.ids.home.fire('click',{target:{closest:()=>({dataset:{open:'helper'}})}});await w.flush();const afterTile=w.ui.state();
  return{net,okfalse,afterBack,afterTile}},
};
(async()=>{const r={};for(const k of Object.keys(S))r[k]=await S[k]();console.log(JSON.stringify(r))})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


class UXP31Static(unittest.TestCase):
    def test_builtin_text_and_quiet_words(self):
        out = page("t" * 32, "n")
        # Why? identifies rules text; the separate chat keeps its existing no-answer label.
        self.assertIn("Rules text. The helper did not answer this request.",
                      (STATIC / "js-5.js").read_text(encoding="utf-8"))
        self.assertIn(STD, out)
        self.assertIn(REST, out)
        self.assertNotIn("Standard answer", out)
        self.assertNotIn("Showing the standard answer", out)
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        mod = js[js.index("UXP31"):]
        for t in ("'Send'", "'Cancel'", "'Retry'", "'Clear'", "Waiting...", "AbortController", "ASK_WAIT_MS=125000", "MAX=6"):
            self.assertIn(t, mod)
        self.assertNotIn("innerHTML", mod)
        self.assertIn("'Something went wrong. Try again in a minute.'", mod)
        self.assertNotIn("in OPOS", mod)
        visible = " ".join(re.findall(r"'([^']*[A-Za-z][^']*)'", mod))
        for word in ("AI", "model", "governor", "assistant"):
            self.assertNotRegex(visible, r"\b%s\b" % word)
        # Built lazily: none of the screen's ids are in the served HTML.
        for i in ("hs-log", "hs-send", "hs-status"):
            self.assertNotIn('id="%s"' % i, out)
        css = (STATIC / "phone-b.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"#hs-bar button\{[^}]*min-height:48px")

    def test_hidden_toolbar_has_no_flex_override(self):
        out = page("t" * 32, "n")
        self.assertRegex(out, r"#hs-bar:not\(\[hidden\]\)\{[^}]*display:flex")
        self.assertNotRegex(out, r"#hs-bar\s*\{[^}]*display:")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class UXP31HelperScreenJS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = subprocess.run(["node", "-e", HARNESS, str(STATIC / "phone-b.js"), WHY, json.dumps(assist.answer("what is paused?", env={}, free=0, context={"mode": "CRITICAL"}))], capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, p.stderr
        cls.r = json.loads(p.stdout.strip().splitlines()[-1])

    def setUp(self):
        self.assertNotIn("error", self.r)

    def test_critical_rest_reply_is_not_a_transport_failure(self):
        r = self.r["restresponse"]
        a = r["rested"]
        self.assertIn("resting to save power", a["log"][-1])
        self.assertNotIn(STD, a["log"][-1])
        self.assertEqual(a["status"], REST)
        self.assertTrue(a["sendDisabled"])
        self.assertTrue(a["retryHidden"])
        self.assertEqual((a["liveTimers"], a["liveIntervals"]), (0, 0))
        self.assertEqual(r["repeated"]["fetches"], 1)
        self.assertFalse(r["recovered"]["sendDisabled"])
        self.assertEqual(r["recovered"]["status"], "")
        self.assertEqual(r["after"]["fetches"], 2)
        self.assertEqual(r["after"]["status"], "Answered.")

    def test_delayed_rest_response_does_not_override_a_newer_recovered_mode(self):
        r = self.r["restrecoveryrace"]
        for mode in ("NORMAL", "SAVER"):
            with self.subTest(mode=mode):
                a = r[mode]
                self.assertFalse(a["sendDisabled"])
                self.assertEqual(a["status"], "This request rested to save power. You can ask again.")
                self.assertTrue(a["retryHidden"])
                self.assertIn("resting to save power", a["log"][-1])
                self.assertNotIn(STD, a["log"][-1])
        self.assertTrue(r["CRITICAL"]["sendDisabled"])
        self.assertEqual(r["CRITICAL"]["status"], REST)

    def test_clearing_history_preserves_the_current_rest_status(self):
        r = self.r["restresponse"]["cleared"]
        self.assertEqual(r["log"], [])
        self.assertTrue(r["sendDisabled"])
        self.assertEqual(r["status"], REST)

    def test_goal_driven_critical_with_usable_charge_keeps_the_helper_answering(self):
        r = self.r["goalcritical"]
        self.assertFalse(r["36"]["sendDisabled"])
        self.assertNotIn("resting", r["36"]["status"])
        self.assertTrue(r["9"]["sendDisabled"])
        self.assertEqual(r["9"]["status"], REST)

    def test_known_critical_mode_blocks_only_new_asks_until_recovery(self):
        r = self.r["reststate"]
        self.assertTrue(r["opened"]["sendDisabled"])
        self.assertEqual(r["opened"]["status"], REST)
        self.assertEqual(r["blocked"]["fetches"], 0)
        self.assertFalse(r["recovered"]["sendDisabled"])
        self.assertEqual(r["after"]["fetches"], 1)
        self.assertEqual(r["after"]["status"], "Answered.")

    def test_critical_state_update_preserves_cancel_and_stale_reply_protection(self):
        r = self.r["restduringwait"]
        self.assertIn("Waiting...", r["waiting"]["status"])
        self.assertFalse(r["waiting"]["cancelDisabled"])
        self.assertTrue(r["cancelled"]["sendDisabled"])
        self.assertTrue(r["cancelled"]["retryHidden"])
        self.assertEqual(r["cancelled"]["status"], REST)
        self.assertEqual(r["after"]["log"], r["cancelled"]["log"])
        self.assertFalse(any("Stale answer" in text for text in r["after"]["log"]))
        self.assertEqual((r["after"]["liveTimers"], r["after"]["liveIntervals"]), (0, 0))

    def test_shared_ask_preserves_fallback_payload_and_cancel(self):
        r = self.r["fallback"]
        self.assertEqual(r["payload"], {"action": "ask", "text": "draft", "web": True})
        self.assertEqual(r["reply"], {"error": "Tap Show helper first"})
        self.assertIsNone(r["cancelled"])

    def test_simulation_disclosure_requires_explicit_server_signal(self):
        r = self.r["backends"]
        self.assertEqual(r["simulated"], "Demo: calls and texts are simulated.")
        self.assertTrue(r["helper"].startswith("Demo phone. "))
        for key in ("absent", "external", "unknown"):
            self.assertEqual(r[key], "Calls and texts may use your configured phone connection.")
            self.assertNotIn("simulated", r[key])

    def test_slow_reply_is_not_aborted_at_fifteen_seconds(self):
        r = self.r["slowreply"]
        for key, elapsed in (("at15", 15), ("at80", 80), ("at120", 120)):
            self.assertEqual(r[key]["aborted"], [False])
            self.assertIn(f"{elapsed} s elapsed", r[key]["status"])
            self.assertEqual(r[key]["fetches"], 1)
        self.assertIn("Real delayed answer", r["after"]["log"][-1])
        self.assertIn("reply: Muse Glimmer", r["after"]["log"][-1])
        self.assertEqual((r["after"]["liveTimers"], r["after"]["liveIntervals"]), (0, 0))

    def test_stalled_response_body_has_bounded_wait(self):
        r = self.r["stuckbody"]
        self.assertEqual(r["deadline"], 125000)
        self.assertEqual(r["after"]["aborted"], [True])
        self.assertFalse(r["after"]["sendDisabled"])
        self.assertFalse(r["after"]["retryHidden"])
        self.assertIn("125 s", r["after"]["status"])
        self.assertEqual(r["after"]["liveIntervals"], 0)

    def test_cancel_settles_before_late_reply_and_retry_ignores_it(self):
        r = self.r["latecancel"]
        self.assertEqual(r["inFlight"]["fetches"], 1)
        self.assertFalse(r["cancelled"]["sendDisabled"])
        self.assertFalse(r["cancelled"]["retryHidden"])
        self.assertEqual(r["cancelled"]["status"], "Cancelled.")
        self.assertEqual(r["retried"]["texts"], ["first", "first"])
        self.assertEqual(r["after"]["log"], r["retried"]["log"])
        self.assertFalse(any("Stale answer" in text for text in r["after"]["log"]))

    def test_http_errors_and_empty_success_are_retryable(self):
        r = self.r["httpempty"]
        for kind, message in (("403", "Reload"), ("429", "Too many"), ("500", "Something went wrong")):
            self.assertIn(message, r[kind]["status"])
            self.assertFalse(r[kind]["retryHidden"])
        self.assertTrue(r["empty"]["log"][-1].endswith("Helper: " + STD + " You can try again."))
        self.assertEqual(r["empty"]["status"], "No reply. Tap Retry.")
        self.assertNotIn("Muse Glimmer", r["empty"]["log"][-1])
        self.assertNotEqual(r["empty"]["status"], "Answered.")
        self.assertFalse(r["empty"]["retryHidden"])

    def test_conversation_shows_hidden_hint_and_other_errors(self):
        r = self.r["errors"]
        for state, message in [(r["hidden"], "Tap Show helper first"),
                               (r["other"], "Try again later.")]:
            self.assertIn(message, state["log"][-1])
            self.assertEqual(state["status"], message)
            self.assertFalse(state["sendDisabled"])
            self.assertTrue(state["cancelDisabled"])
            self.assertFalse(state["retryHidden"])

    def test_lazy_first_open_one_toggle(self):
        r = self.r["lazy"]
        b = r["before"]
        self.assertEqual((b["toggles"], b["fetches"], b["timers"]), (0, 0, 0))
        self.assertFalse(b["hsExists"])
        self.assertEqual(b["app"], "home")
        f = r["first"]
        self.assertEqual(f["toggles"], 1)
        self.assertEqual(f["fetches"], 0)  # opening never asks a question on its own
        self.assertEqual(f["app"], "helper")
        self.assertEqual(f["askHs"], "1")
        self.assertEqual(f["live"], "polite")
        self.assertEqual((f["sendDisabled"], f["cancelDisabled"], f["retryHidden"]), (False, True, True))
        self.assertEqual(r["again"]["toggles"], 1)

    def test_cancel_aborts_and_reenables_send(self):
        r = self.r["cancel"]
        w = r["waiting"]
        self.assertIn("Waiting... 0 s elapsed", w["status"])
        self.assertTrue(w["sendDisabled"])
        self.assertFalse(w["cancelDisabled"])
        self.assertEqual(w["aborted"], [False])
        self.assertEqual(w["actions"], ["ask"])
        self.assertEqual(r["navWhileWaiting"], "home")  # the shell still responds while waiting
        a = r["after"]
        self.assertEqual(a["aborted"], [True])
        self.assertFalse(a["sendDisabled"])
        self.assertTrue(a["cancelDisabled"])
        self.assertEqual(a["status"], "Cancelled.")
        self.assertEqual(a["fetches"], 1)

    def test_retry_resends_last_question(self):
        r = self.r["retry"]
        self.assertFalse(r["failed"]["retryHidden"])
        self.assertIn("Something went wrong", r["failed"]["log"][-1])
        a = r["after"]
        self.assertEqual(a["texts"], ["hello", "hello"])
        self.assertTrue(a["retryHidden"])
        self.assertIn("A:hello", a["log"][-1])

    def test_timeout_falls_back_to_builtin_text(self):
        r = self.r["timeout"]
        self.assertEqual(r["timers"], 1)
        a = r["after"]
        self.assertEqual(a["aborted"], [True])
        self.assertTrue(a["log"][-1].endswith("Helper: " + STD + " You can try again."))
        self.assertNotIn(WHY, a["log"][-1])  # the Why? text is not shown as if it answered
        self.assertEqual(a["status"], "No reply after 125 s. Tap Retry.")
        self.assertFalse(a["sendDisabled"])
        self.assertFalse(a["retryHidden"])
        self.assertNotEqual(a["status"], "Waiting...")

    def test_seventh_turn_drops_first_and_clear_empties(self):
        r = self.r["bound"]
        f = r["full"]
        self.assertEqual(f["fetches"], 7)  # odd turns by Send, even turns by Enter
        self.assertEqual(len(f["log"]), 6)
        self.assertIn("You: q2", f["log"][0])
        self.assertIn("A:q7", f["log"][-1])
        self.assertFalse(any("q1 " in t or t.endswith("q1") for t in f["log"]))
        c = r["cleared"]
        self.assertEqual(c["log"], [])
        self.assertTrue(c["logHidden"])

    def test_answers_are_announced(self):
        r = self.r["announce"]
        st = r["st"]
        self.assertEqual((st["logRole"], st["logLive"]), ("log", "polite"))
        self.assertEqual(st["status"], "Answered.")
        self.assertIn("A:two", st["log"][-1])
        self.assertTrue(r["sameRow"])  # old rows are kept, so only the new turn is announced

    def test_wikipedia_sources_kept_and_checked(self):
        r = self.r["sources"]
        self.assertIn("Wiki says hi", r["log"][-1])
        self.assertEqual([l["t"] for l in r["links"]], ["Battery", "Bad"])
        self.assertEqual(r["links"][0]["href"], "https://en.wikipedia.org/wiki/Battery")
        self.assertEqual(r["links"][1]["href"], "")  # only Wikipedia URLs become links
        self.assertEqual(r["links"][0]["rel"], "noopener noreferrer")

    def test_no_abortcontroller_keeps_ask(self):
        r = self.r["noabort"]
        self.assertFalse(r["hs"])
        self.assertEqual(r["st"]["askHs"], "")
        self.assertEqual(r["st"]["app"], "helper")
        self.assertEqual(r["st"]["toggles"], 1)

    def test_sync_fetch_error_gives_ask_back(self):
        r = self.r["throwfetch"]
        st = r["st"]
        self.assertEqual(st["askHs"], "")  # the Ask button shows again
        self.assertTrue(st["barHidden"])
        self.assertTrue(st["logHidden"])
        self.assertNotEqual(st["status"], "Waiting...")
        self.assertEqual(r["again"]["askHs"], "")  # Enter is no longer taken over
        self.assertEqual(r["app"], "home")

    def test_response_render_error_gives_ask_back(self):
        r = self.r["renderfail"]
        st = r["st"]
        self.assertEqual(st["fetches"], 1)
        self.assertEqual(st["askHs"], "")
        self.assertTrue(st["barHidden"])
        self.assertTrue(st["logHidden"])
        self.assertEqual(st["status"], "")
        self.assertEqual(r["liveTimers"], 0)
        self.assertFalse(r["prevented"])  # original Enter handling remains available
        self.assertEqual(r["home"], "home")
        self.assertEqual(r["again"]["app"], "helper")
        self.assertEqual(r["again"]["askHs"], "")
        self.assertEqual(r["again"]["fetches"], 1)

    def test_failure_keeps_home_and_back_working(self):
        r = self.r["failure"]
        self.assertTrue(r["net"]["log"][-1].endswith("Helper: " + STD + " You can try again."))
        self.assertFalse(r["net"]["sendDisabled"])
        # A reason from the server stands alone: no second "did not reply" in front of it.
        self.assertTrue(r["okfalse"]["log"][-1].endswith("Helper: There is no answer right now."))
        self.assertNotIn(STD, r["okfalse"]["log"][-1])
        self.assertEqual(r["afterBack"], "home")
        t = r["afterTile"]
        self.assertEqual(t["app"], "helper")
        self.assertEqual(t["toggles"], 1)
        self.assertEqual(len(t["log"]), 2)  # memory survives leaving and coming back


if __name__ == "__main__":
    unittest.main()
