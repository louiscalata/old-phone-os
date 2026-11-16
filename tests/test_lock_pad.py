# SPDX-License-Identifier: GPL-3.0-or-later
"""The PIN pad script (web_static/pin.js), run in node against a stand-in page and a stand-in /api/lock.

The page's lock screen is a cover. These tests check that, with the script loaded, the cover comes off only after the
server's own answer says the phone is unlocked, and that showing the cover locks the server. No browser is used.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

JS = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static" / "pin.js"
NODE = shutil.which("node")

HARNESS = r"""
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8');
function world(server,opts){
  opts=opts||{};
  class Element{
    constructor(id){this.id=id||'';this.hidden=false;this.children=[];this.attrs={};this.style={};this.text='';this.parentNode=null}
    setAttribute(k,v){this.attrs[k]=String(v)}
    appendChild(e){e.parentNode=this;this.children.push(e);return e}
    insertBefore(e,ref){e.parentNode=this;const i=this.children.indexOf(ref);this.children.splice(i<0?this.children.length:i,0,e);return e}
    get textContent(){return this.text}
    set textContent(v){this.text=String(v)}
  }
  const ids={},root=new Element('root');
  for(const id of ['phone','lock','unlock','lock-notice']){ids[id]=new Element(id);(id==='unlock'||id==='lock-notice'?ids.lock:root).appendChild(ids[id])}
  const token=new Element();token.content='tok';
  const requests=[],timers=[],calls=[];
  const cover=ids.lock,phone=ids.phone;
  cover.hidden=!!opts.coverHidden;phone.inert=!cover.hidden;
  const ui={
    unlock(){calls.push('uncover');cover.hidden=true;phone.inert=false},
    lock(){calls.push('cover');cover.hidden=false;phone.inert=true},
  };
  const c={
    document:{
      querySelector:s=>s==='meta[name=opos-token]'&&!opts.noToken?token:null,
      getElementById:id=>ids[id]||null,
      createElement:tag=>{const e=new Element();e.tag=tag;return e},
      addEventListener(){},
    },
    setInterval:(fn,ms)=>{timers.push([fn,ms]);return timers.length},
    fetch(url,options){
      const body=options.body?JSON.parse(options.body):null;
      requests.push({url,method:options.method||'GET',token:options.headers['X-OPOS-Token'],type:options.headers['Content-Type']||'',body});
      if(server.down===true||(server.down==='post'&&body))return Promise.reject(new Error('down'));
      const answer=server.answer(body);
      return Promise.resolve({status:server.code||200,json:()=>Promise.resolve(answer)});
    },
    JSON,Math,Promise,Error,String,
  };
  if(!opts.noUI)c.oposUI=ui;
  c.window=c;
  ids.unlock.onclick=()=>c.oposUI.unlock();
  vm.runInNewContext(source,c);
  const pad=()=>cover.children.find(e=>e.id==='lock-pad')||null;
  const key=label=>pad().children.find(e=>e.tag==='button'&&e.textContent===label).onclick();
  const line=()=>{const p=pad();return p?p.children[0].textContent:''};
  return {c,cover,phone,opener:ids.unlock,requests,timers,calls,pad,key,line,ui:()=>c.oposUI,ids};
}
function phoneServer(pin){
  const s={locked:true,pin:pin,wait_s:0,down:false,useOdd:false,odd:null,log:[]};
  const status=()=>({ok:!s.locked,locked:s.locked,has_pin:s.pin!==null,wait_s:s.locked?s.wait_s:0,failures:0});
  s.answer=body=>{
    s.code=200;
    if(s.useOdd){s.code=s.oddCode||200;return s.odd}
    if(!body)return Object.assign(status(),{enforced:true,lock:{messages:0,missed:0,alarm:''}});
    s.log.push(body.action);
    if(s.refuse&&body.action==='unlock'){s.code=s.refuseCode||200;return s.refuse}
    if(s.refuseLock&&body.action==='lock'){s.code=s.refuseLock.code;return s.refuseLock.body}
    if(body.action==='lock'){s.locked=true;return status()}
    if(body.action==='unlock'){
      if(s.wait_s>0)return status();
      if(s.pin===null||body.pin===s.pin)s.locked=false;
      return status();
    }
    return {error:'bad request'};
  };
  return s;
}
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r))};
const posts=w=>w.requests.filter(r=>r.method==='POST').map(r=>r.body);
const count=(w,action)=>posts(w).filter(b=>b.action===action).length;
const gets=w=>w.requests.filter(r=>r.method==='GET').length;
const typed=async(w,digits)=>{for(const d of digits)w.key(d);w.key('Unlock');await flush()};
// An unlocked phone with the cover off: the state every refused-Lock case starts from.
const unlocked=async pin=>{
  const s=phoneServer(pin),w=world(s);
  await flush();
  w.opener.onclick();await flush();
  if(pin!==null)await typed(w,pin);
  if(!w.cover.hidden||s.locked)throw new Error('the stand-in phone did not unlock');
  w.requests.length=0;w.calls.length=0;
  return {s,w};
};
const FAILS={
  'busy 429':s=>{s.refuseLock={code:429,body:{error:'too many requests'}}},
  'connection':s=>{s.down='post'},
  'answer unlocked':s=>{s.refuseLock={code:200,body:{ok:true,locked:false,has_pin:true,wait_s:0,failures:0}}},
  'answer without locked':s=>{s.refuseLock={code:200,body:{ok:true}}},
  'answer null':s=>{s.refuseLock={code:200,body:null}},
  'answer 500':s=>{s.refuseLock={code:500,body:{error:'internal error'}}},
};
const mend=s=>{s.refuseLock=null;s.down=false};
(async()=>{
  const out={};
  {
    // A saved PIN: the pad, a wrong try, then the right digits.
    const s=phoneServer('1234'),w=world(s);
    await flush();
    out.loadLocks=posts(w).map(b=>b.action);
    out.headers=[w.requests[0].url,w.requests[0].token,w.requests.find(r=>r.method==='POST').type];
    out.poll=w.timers.map(t=>t[1]);
    w.requests.length=0;
    w.opener.onclick();await flush();
    out.afterTap={cover:w.cover.hidden,inert:w.phone.inert,pad:!!w.pad()&&!w.pad().hidden,opener:w.opener.hidden,line:w.line(),posts:posts(w)};
    out.keys=w.pad().children.filter(e=>e.tag==='button').map(e=>e.textContent);
    out.padBefore=w.cover.children.indexOf(w.pad())<w.cover.children.indexOf(w.opener);
    w.key('9');out.one=w.line();
    w.key('9');w.key('9');w.key('9');out.four=w.line();
    w.key('Unlock');await flush();
    out.wrong={cover:w.cover.hidden,inert:w.phone.inert,line:w.line(),posts:posts(w),uncovered:w.calls.includes('uncover')};
    w.requests.length=0;
    w.key('1');w.key('2');w.key('Clear');out.cleared=w.line();
    w.key('1');w.key('2');w.key('3');w.key('4');w.key('Unlock');await flush();
    out.right={cover:w.cover.hidden,inert:w.phone.inert,pad:w.pad().hidden,opener:w.opener.hidden,posts:posts(w),server:s.locked};
    // Locking from the page locks the server and puts the plain Unlock control back.
    w.requests.length=0;
    w.ui().lock();await flush();
    out.relock={cover:w.cover.hidden,posts:posts(w),server:s.locked,pad:w.pad().hidden,opener:w.opener.hidden};
    // Too short is not sent; more than eight digits are not kept.
    w.opener.onclick();await flush();w.requests.length=0;
    w.key('1');w.key('2');w.key('Unlock');await flush();
    out.short={line:w.line(),sent:w.requests.length,cover:w.cover.hidden};
    for(const d of '1234567890')w.key(d);
    out.long=w.line();
    w.key('Unlock');await flush();
    out.longSent=posts(w).map(b=>b.pin);
  }
  {
    // A running wait is said in plain units, and the cover stays.
    const s=phoneServer('1234'),w=world(s);
    await flush();
    out.waits=[];
    for(const n of [30,119,120,3599,3600,950400]){
      s.wait_s=n;w.opener.onclick();await flush();
      const shown=w.line();
      for(const d of '1234')w.key(d);
      w.key('Unlock');await flush();
      out.waits.push([n,shown,w.line(),w.cover.hidden]);
    }
  }
  {
    // No saved PIN: main's rule is that the lock opens without digits. The server still has to say so.
    const s=phoneServer(null),w=world(s);
    await flush();w.requests.length=0;
    w.opener.onclick();
    out.noPinBeforeAnswer=w.cover.hidden;
    await flush();
    out.noPin={cover:w.cover.hidden,inert:w.phone.inert,posts:posts(w),pad:!!w.pad(),server:s.locked};
    // With the cover already off, unlock (the shade calls it on every open) asks the server nothing.
    w.requests.length=0;w.ui().unlock();await flush();
    out.shade=w.requests.length;
    // The server was locked elsewhere: the next check covers the page without another lock request.
    s.locked=true;w.requests.length=0;
    w.timers[0][0]();await flush();
    out.covered={cover:w.cover.hidden,inert:w.phone.inert,posts:posts(w)};
    // The server's own "locked" answer is the confirmation: later checks do not send a Lock request.
    w.timers[0][0]();await flush();
    out.coveredLater=posts(w);
    // A check that was asked before an unlock and answered after it does not cover the phone again.
    w.opener.onclick();await flush();
    let late=null;const realFetch=w.c.fetch;
    w.c.fetch=(url,options)=>options.method?realFetch(url,options):new Promise(r=>{late=()=>r({json:()=>Promise.resolve({locked:true,has_pin:false,enforced:true})})});
    w.timers[0][0]();
    w.c.fetch=realFetch;
    s.locked=true;w.ui().lock();await flush();
    w.opener.onclick();await flush();
    out.beforeLate=w.cover.hidden;
    late();await flush();
    out.afterLate=w.cover.hidden;
  }
  {
    // Answers that are not a plain "unlocked" never take the cover off.
    out.closed=[];
    for(const odd of [{error:'forbidden'},{locked:'false'},{locked:0},{ok:true},null,[],'unlocked']){
      const s=phoneServer('1234'),w=world(s);
      await flush();
      s.odd=odd;s.useOdd=true;w.opener.onclick();await flush();
      if(w.pad()&&!w.pad().hidden&&w.pad().children.length>1){for(const d of '1234')w.key(d);w.key('Unlock');await flush()}
      out.closed.push([JSON.stringify(odd),w.cover.hidden,w.phone.inert]);
    }
    const s=phoneServer('1234'),w=world(s);
    await flush();
    s.down=true;w.opener.onclick();await flush();
    out.down={cover:w.cover.hidden,line:w.line()};
    s.down=false;w.opener.onclick();await flush();
    for(const d of '1234')w.key(d);
    s.down=true;w.key('Unlock');await flush();
    out.downOnSubmit={cover:w.cover.hidden,line:w.line()};
    s.down=false;for(const d of '1234')w.key(d);w.key('Unlock');await flush();
    out.recovered=w.cover.hidden;
    const n=phoneServer(null),v=world(n);
    await flush();
    n.down=true;v.opener.onclick();await flush();
    out.noPinDown=v.cover.hidden;
    out.noPinRefused=[];
    for(const refuse of [{error:'locked'},{locked:true,has_pin:false},{ok:true}]){
      const r=phoneServer(null),u=world(r);
      await flush();
      r.refuse=refuse;u.opener.onclick();await flush();
      out.noPinRefused.push([u.cover.hidden,u.phone.inert,u.line()]);
    }
  }
  {
    // The Lock request is refused or lost while the phone is unlocked with a saved PIN. The page shows its cover and
    // the server is still unlocked. Its "unlocked" status must not take the cover off: only a PIN does.
    out.refused=[];
    for(const how of Object.keys(FAILS)){
      const {s,w}=await unlocked('1234');
      FAILS[how](s);
      w.ui().lock();await flush();await flush();
      const row={how,lock:{cover:w.cover.hidden,inert:w.phone.inert,server:s.locked,line:w.line(),locks:count(w,'lock')}};
      // Unlock is tapped twice while the server still refuses to lock.
      w.opener.onclick();await flush();
      w.opener.onclick();await flush();
      row.tap={cover:w.cover.hidden,inert:w.phone.inert,uncovered:w.calls.includes('uncover'),line:w.line(),
               locks:count(w,'lock'),unlocks:count(w,'unlock'),gets:gets(w)};
      // The pad's own Unlock key does not send digits to a phone that did not lock either.
      row.keys=!!w.pad()&&!w.pad().hidden;
      if(row.keys){await typed(w,'9999');row.typed={cover:w.cover.hidden,unlocks:count(w,'unlock'),line:w.line()}}
      // The next check asks again, once.
      const before=count(w,'lock');
      w.timers[0][0]();await flush();await flush();
      row.tick={cover:w.cover.hidden,locks:count(w,'lock')-before,server:s.locked};
      // The server answers again: the lock goes through first, and then the pad asks for the PIN.
      mend(s);w.requests.length=0;
      w.opener.onclick();await flush();
      row.mended={cover:w.cover.hidden,server:s.locked,pad:!!w.pad()&&!w.pad().hidden,opener:w.opener.hidden,line:w.line(),
                  order:w.requests.map(r=>r.method==='GET'?'status':r.body.action)};
      await typed(w,'9999');
      row.wrong={cover:w.cover.hidden,line:w.line()};
      await typed(w,'1234');
      row.right={cover:w.cover.hidden,inert:w.phone.inert,server:s.locked};
      out.refused.push(row);
    }
    {
      // The check retries a failed lock by itself; once it goes through, the failure line goes away.
      const {s,w}=await unlocked('1234');
      FAILS['busy 429'](s);
      w.ui().lock();await flush();
      mend(s);
      w.timers[0][0]();await flush();await flush();
      out.selfMended={cover:w.cover.hidden,server:s.locked,locks:count(w,'lock'),line:w.line(),pad:w.pad().hidden,opener:w.opener.hidden};
      // A confirmed lock is not asked for again by later checks.
      w.timers[0][0]();await flush();w.timers[0][0]();await flush();
      out.selfMendedLater=count(w,'lock');
    }
    {
      // The page loads with its cover on while the server is unlocked and refuses the load-time Lock.
      const s=phoneServer('1234');s.locked=false;FAILS['busy 429'](s);
      const w=world(s);
      await flush();
      w.opener.onclick();await flush();
      out.refusedAtLoad={cover:w.cover.hidden,uncovered:w.calls.includes('uncover'),server:s.locked,line:w.line()};
    }
    {
      // Unlock is tapped while the Lock request is still on its way: nothing is decided before its answer.
      const {s,w}=await unlocked('1234');
      let release=null;const realFetch=w.c.fetch;
      w.c.fetch=(url,options)=>{
        if(!options.method)return realFetch(url,options);
        w.requests.push({url,method:'POST',body:JSON.parse(options.body)});
        return new Promise(r=>{release=()=>r({status:200,json:()=>Promise.resolve(s.answer({action:'lock'}))})});
      };
      w.ui().lock();
      w.opener.onclick();await flush();
      w.opener.onclick();await flush();
      out.inFlight={cover:w.cover.hidden,uncovered:w.calls.includes('uncover'),locks:count(w,'lock'),gets:gets(w)};
      w.c.fetch=realFetch;
      release();await flush();
      out.inFlightAfter={cover:w.cover.hidden,server:s.locked,pad:!!w.pad()&&!w.pad().hidden,line:w.line()};
    }
    {
      // No saved PIN: the same rule. A refused Lock keeps the cover; after a confirmed one the server opens it.
      const {s,w}=await unlocked(null);
      FAILS['busy 429'](s);
      w.ui().lock();await flush();
      w.opener.onclick();await flush();
      out.noPinRefusedLock={cover:w.cover.hidden,uncovered:w.calls.includes('uncover'),unlocks:count(w,'unlock')};
      mend(s);w.requests.length=0;
      w.opener.onclick();await flush();
      out.noPinMended={cover:w.cover.hidden,order:w.requests.map(r=>r.method==='GET'?'status':r.body.action)};
    }
    {
      // A 429 to the digits is not called a wrong PIN.
      const s=phoneServer('1234'),w=world(s);
      await flush();
      w.opener.onclick();await flush();
      s.refuse={error:'too many requests'};s.refuseCode=429;
      await typed(w,'1234');
      out.busyUnlock={cover:w.cover.hidden,line:w.line()};
      s.refuse=null;
      await typed(w,'1234');
      out.busyUnlockAfter=w.cover.hidden;
    }
    {
      // "Unlocked" inside an error answer (403, 500) is not an unlock: to the status request, or to the digits.
      out.errorCodes=[];
      const yes={ok:true,locked:false,has_pin:true,wait_s:0,failures:0};
      for(const code of [403,500,201]){
        const s=phoneServer('1234'),w=world(s);
        await flush();
        s.odd=yes;s.oddCode=code;s.useOdd=true;
        w.opener.onclick();await flush();
        const status=w.cover.hidden;
        s.useOdd=false;
        w.opener.onclick();await flush();
        s.refuse=yes;s.refuseCode=code;
        await typed(w,'9999');
        out.errorCodes.push([code,status,w.cover.hidden,w.calls.includes('uncover')]);
      }
    }
    {
      const s=phoneServer('1234'),w=world(s);
      await flush();
      w.opener.onclick();await flush();
      out.padStyle=w.pad().style.cssText;
      out.lineStyle=w.pad().children[0].style.cssText;
      out.keyStyles=[...new Set(w.pad().children.filter(e=>e.tag==='button').map(e=>e.style.cssText))];
    }
  }
  {
    // A page without its lock parts is left alone.
    const a=world(phoneServer('1234'),{noUI:true});await flush();
    const b=world(phoneServer('1234'),{noToken:true});await flush();
    out.inert=[a.requests.length,a.timers.length,b.requests.length,b.timers.length,b.ui().unlock===b.c.oposUI.unlock];
    b.opener.onclick();
    out.untouched=b.cover.hidden;
    // The cover is off at load (for example ?lock=0) while the server is locked: the first check covers the page.
    const s=phoneServer('1234'),w=world(s,{coverHidden:true});
    await flush();
    out.lateCover={cover:w.cover.hidden,posts:posts(w)};
  }
  console.log(JSON.stringify(out));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1)});
"""


class LockPad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest("node not installed")
        result = subprocess.run(
            [NODE, "--unhandled-rejections=strict", "-e", HARNESS, str(JS)], capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_showing_the_cover_locks_the_server(self):
        out = self.out
        self.assertEqual(out["loadLocks"], ["lock"])
        self.assertEqual(out["headers"], ["/api/lock", "tok", "application/json"])
        self.assertEqual(out["poll"], [5000])
        self.assertEqual(
            out["relock"], {"cover": False, "posts": [{"action": "lock"}], "server": True, "pad": True, "opener": False}
        )

    def test_a_saved_pin_needs_the_pad_and_the_servers_answer(self):
        out = self.out
        self.assertEqual(
            out["afterTap"],
            {"cover": False, "inert": True, "pad": True, "opener": True, "line": "Enter the PIN", "posts": []},
        )
        self.assertEqual(out["keys"], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "Clear", "0", "Unlock"])
        self.assertTrue(out["padBefore"])
        self.assertEqual(
            (out["one"], out["four"], out["cleared"]), ("1 digit entered", "4 digits entered", "Enter the PIN")
        )
        self.assertEqual(
            out["wrong"],
            {
                "cover": False,
                "inert": True,
                "line": "Wrong PIN",
                "posts": [{"action": "unlock", "pin": "9999"}],
                "uncovered": False,
            },
        )
        self.assertEqual(
            out["right"],
            {
                "cover": True,
                "inert": False,
                "pad": True,
                "opener": False,
                "posts": [{"action": "unlock", "pin": "1234"}],
                "server": False,
            },
        )

    def test_digits_are_never_shown_and_the_length_is_bounded(self):
        out = self.out
        self.assertEqual(out["short"], {"line": "A PIN has 4 to 8 digits", "sent": 0, "cover": False})
        self.assertEqual(out["long"], "8 digits entered")
        self.assertEqual(out["longSent"], ["12345678"])
        for text in (out["one"], out["four"], out["long"], out["wrong"]["line"]):
            self.assertNotRegex(
                text.replace("4 to 8", "").replace("8 digits", "").replace("4 digits", "").replace("1 digit", ""), r"\d"
            )

    def test_a_wait_is_said_in_plain_units_and_keeps_the_cover(self):
        expected = {30: "30 s", 119: "119 s", 120: "2 min", 3599: "60 min", 3600: "about 1 h", 950400: "about 264 h"}
        self.assertEqual([row[0] for row in self.out["waits"]], list(expected))
        for seconds, shown, after, cover_hidden in self.out["waits"]:
            text = "Too many wrong tries. Wait %s." % expected[seconds]
            self.assertEqual((shown, after, cover_hidden), (text, text, False), seconds)

    def test_no_saved_pin_opens_without_digits_only_after_the_server_answers(self):
        out = self.out
        self.assertFalse(out["noPinBeforeAnswer"])
        self.assertEqual(
            out["noPin"],
            {"cover": True, "inert": False, "posts": [{"action": "unlock", "pin": ""}], "pad": False, "server": False},
        )
        self.assertEqual(out["shade"], 0)
        self.assertEqual(out["covered"], {"cover": False, "inert": True, "posts": []})
        self.assertEqual(out["coveredLater"], [])
        self.assertEqual((out["beforeLate"], out["afterLate"]), (True, True))
        self.assertEqual(out["lateCover"], {"cover": False, "posts": []})

    def test_anything_but_a_plain_unlocked_answer_keeps_the_cover(self):
        out = self.out
        self.assertEqual(len(out["closed"]), 7)
        for odd, cover_hidden, inert in out["closed"]:
            self.assertEqual((cover_hidden, inert), (False, True), odd)
        self.assertEqual(out["down"], {"cover": False, "line": "The lock did not answer. Reload the page."})
        self.assertEqual(out["downOnSubmit"], {"cover": False, "line": "The phone did not answer. Try again."})
        self.assertTrue(out["recovered"])
        self.assertFalse(out["noPinDown"])
        self.assertEqual(out["noPinRefused"], [[False, True, "The lock did not answer. Reload the page."]] * 3)

    FAILED = "The phone did not lock. Unlock tries again."

    def test_a_refused_lock_keeps_the_cover_and_never_skips_the_pin(self):
        rows = self.out["refused"]
        self.assertEqual(
            [row["how"] for row in rows],
            ["busy 429", "connection", "answer unlocked", "answer without locked", "answer null", "answer 500"],
        )
        for row in rows:
            with self.subTest(how=row["how"]):
                # The cover is on, the server is not locked, and the page says so.
                self.assertEqual(
                    row["lock"], {"cover": False, "inert": True, "server": False, "line": self.FAILED, "locks": 1}
                )
                # Two taps: the lock is asked for again each time, the status is not, and nothing opens.
                self.assertEqual(
                    row["tap"],
                    {
                        "cover": False,
                        "inert": True,
                        "uncovered": False,
                        "line": self.FAILED,
                        "locks": 3,
                        "unlocks": 0,
                        "gets": 0,
                    },
                )
                if row["keys"]:
                    self.assertEqual(row["typed"], {"cover": False, "unlocks": 0, "line": self.FAILED})
                self.assertEqual(row["tick"], {"cover": False, "locks": 1, "server": False})
                self.assertEqual(
                    row["mended"],
                    {
                        "cover": False,
                        "server": True,
                        "pad": True,
                        "opener": True,
                        "line": "Enter the PIN",
                        "order": ["lock", "status"],
                    },
                )
                self.assertEqual(row["wrong"], {"cover": False, "line": "Wrong PIN"})
                self.assertEqual(row["right"], {"cover": True, "inert": False, "server": False})

    def test_a_failed_lock_is_retried_by_the_check_and_not_in_a_loop(self):
        out = self.out
        self.assertEqual(
            out["selfMended"],
            {"cover": False, "server": True, "locks": 2, "line": "Enter the PIN", "pad": True, "opener": False},
        )
        self.assertEqual(out["selfMendedLater"], 2)
        self.assertEqual(
            out["refusedAtLoad"], {"cover": False, "uncovered": False, "server": False, "line": self.FAILED}
        )

    def test_unlock_waits_for_the_lock_request_that_is_on_its_way(self):
        out = self.out
        self.assertEqual(out["inFlight"], {"cover": False, "uncovered": False, "locks": 1, "gets": 0})
        self.assertEqual(out["inFlightAfter"], {"cover": False, "server": True, "pad": True, "line": "Enter the PIN"})

    def test_no_saved_pin_still_needs_a_confirmed_lock(self):
        out = self.out
        self.assertEqual(out["noPinRefusedLock"], {"cover": False, "uncovered": False, "unlocks": 0})
        self.assertEqual(out["noPinMended"], {"cover": True, "order": ["lock", "status", "unlock"]})

    def test_a_busy_answer_to_the_digits_is_not_a_wrong_pin(self):
        out = self.out
        self.assertEqual(
            out["busyUnlock"], {"cover": False, "line": "The phone is busy. Wait a few seconds, then try again."}
        )
        self.assertTrue(out["busyUnlockAfter"])

    def test_unlocked_inside_an_error_answer_keeps_the_cover(self):
        self.assertEqual(
            self.out["errorCodes"], [[403, False, False, False], [500, False, False, False], [201, False, False, False]]
        )

    def test_the_pad_fits_a_320_pixel_screen(self):
        """The rules, not a rendering: no browser is used. The three columns can shrink and no key keeps a minimum width."""

        def rules(text):
            return dict(part.split(":", 1) for part in text.split(";") if part)

        pad = rules(self.out["padStyle"])
        self.assertEqual(pad["display"], "grid")
        self.assertEqual(pad["grid-template-columns"], "repeat(3,minmax(0,1fr))")
        self.assertEqual((pad["width"], pad["max-width"], pad["box-sizing"]), ("min(100%,360px)", "100%", "border-box"))
        self.assertEqual(pad.get("padding", "0"), "0")
        gap = int(pad["gap"].removesuffix("px"))
        self.assertEqual(len(self.out["keyStyles"]), 1)
        key = rules(self.out["keyStyles"][0])
        # The page's own "#lock button" rules give every button on the cover a 160px minimum and a 360px width.
        # A style set on the element wins over them unless one is !important.
        self.assertEqual(
            (key["min-width"], key["width"], key["box-sizing"], key["margin"]), ("0", "100%", "border-box", "0")
        )
        self.assertEqual(key["min-height"], "56px")
        self.assertEqual(rules(self.out["lineStyle"])["min-width"], "0")
        static = JS.parent
        sheets = [path.read_text(encoding="utf-8") for path in sorted(static.glob("*.css"))]
        sheets.append((static.parent / "web.py").read_text(encoding="utf-8"))
        found = 0
        for sheet in sheets:
            for selector, body in re.findall(r"([^{}]*#lock[^{}]*button[^{}]*)\{([^{}]*)\}", sheet):
                found += 1
                self.assertNotIn("!important", body, selector)
        self.assertGreaterEqual(found, 2)
        self.assertTrue(any("#lock button{min-height:56px;min-width:160px}" in sheet for sheet in sheets))
        # Before: three keys of at least 160px and two gaps. After: only the gaps cannot shrink.
        self.assertEqual(3 * 160 + 2 * gap, 496)
        narrowest = 3 * int(key["min-width"]) + 2 * gap
        self.assertLessEqual(narrowest, 320)

    def test_a_page_without_its_lock_parts_is_left_alone(self):
        self.assertEqual(self.out["inert"], [0, 0, 0, 0, True])
        self.assertTrue(self.out["untouched"])

    def test_the_script_keeps_no_pin_and_compares_none(self):
        text = JS.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n"))
        for word in ("localStorage", "sessionStorage", "console.", "innerHTML", "eval(", "document.cookie"):
            self.assertNotIn(word, text)
        self.assertEqual(text.count('"/api/lock"'), 1)


if __name__ == "__main__":
    unittest.main()
