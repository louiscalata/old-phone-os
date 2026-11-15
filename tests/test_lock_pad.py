# SPDX-License-Identifier: GPL-3.0-or-later
"""The PIN pad script (web_static/pin.js), run in node against a stand-in page and a stand-in /api/lock.

The page's lock screen is a cover. These tests check that, with the script loaded, the cover comes off only after the
server's own answer says the phone is unlocked, and that showing the cover locks the server. No browser is used.
"""
import json
from pathlib import Path
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
      if(server.down)return Promise.reject(new Error('down'));
      return Promise.resolve({json:()=>Promise.resolve(server.answer(body))});
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
    if(s.useOdd)return s.odd;
    if(!body)return Object.assign(status(),{enforced:true,lock:{messages:0,missed:0,alarm:''}});
    s.log.push(body.action);
    if(s.refuse&&body.action==='unlock')return s.refuse;
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
        result = subprocess.run([NODE, "--unhandled-rejections=strict", "-e", HARNESS, str(JS)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_showing_the_cover_locks_the_server(self):
        out = self.out
        self.assertEqual(out["loadLocks"], ["lock"])
        self.assertEqual(out["headers"], ["/api/lock", "tok", "application/json"])
        self.assertEqual(out["poll"], [5000])
        self.assertEqual(out["relock"], {"cover": False, "posts": [{"action": "lock"}], "server": True, "pad": True, "opener": False})

    def test_a_saved_pin_needs_the_pad_and_the_servers_answer(self):
        out = self.out
        self.assertEqual(out["afterTap"], {"cover": False, "inert": True, "pad": True, "opener": True,
                                           "line": "Enter the PIN", "posts": []})
        self.assertEqual(out["keys"], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "Clear", "0", "Unlock"])
        self.assertTrue(out["padBefore"])
        self.assertEqual((out["one"], out["four"], out["cleared"]), ("1 digit entered", "4 digits entered", "Enter the PIN"))
        self.assertEqual(out["wrong"], {"cover": False, "inert": True, "line": "Wrong PIN",
                                        "posts": [{"action": "unlock", "pin": "9999"}], "uncovered": False})
        self.assertEqual(out["right"], {"cover": True, "inert": False, "pad": True, "opener": False,
                                        "posts": [{"action": "unlock", "pin": "1234"}], "server": False})

    def test_digits_are_never_shown_and_the_length_is_bounded(self):
        out = self.out
        self.assertEqual(out["short"], {"line": "A PIN has 4 to 8 digits", "sent": 0, "cover": False})
        self.assertEqual(out["long"], "8 digits entered")
        self.assertEqual(out["longSent"], ["12345678"])
        for text in (out["one"], out["four"], out["long"], out["wrong"]["line"]):
            self.assertNotRegex(text.replace("4 to 8", "").replace("8 digits", "").replace("4 digits", "").replace("1 digit", ""), r"\d")

    def test_a_wait_is_said_in_plain_units_and_keeps_the_cover(self):
        expected = {30: "30 s", 119: "119 s", 120: "2 min", 3599: "60 min", 3600: "about 1 h", 950400: "about 264 h"}
        self.assertEqual([row[0] for row in self.out["waits"]], list(expected))
        for seconds, shown, after, cover_hidden in self.out["waits"]:
            text = "Too many wrong tries. Wait %s." % expected[seconds]
            self.assertEqual((shown, after, cover_hidden), (text, text, False), seconds)

    def test_no_saved_pin_opens_without_digits_only_after_the_server_answers(self):
        out = self.out
        self.assertFalse(out["noPinBeforeAnswer"])
        self.assertEqual(out["noPin"], {"cover": True, "inert": False, "posts": [{"action": "unlock", "pin": ""}],
                                        "pad": False, "server": False})
        self.assertEqual(out["shade"], 0)
        self.assertEqual(out["covered"], {"cover": False, "inert": True, "posts": []})
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
