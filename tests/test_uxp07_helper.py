# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from opos.web import page

STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"
OFF = "Answers are made on this phone when possible. Web search is off."
ON = "Web search is on: your question is sent to Wikipedia."
LAN = "Answers may be made on a computer you own. Web search is off."

# Tiny fake DOM so phone-b.js can run under node; enough for the helper open flow and the label.
HARNESS = r"""
const vm=require('vm'),fs=require('fs');
function el(id){const L={};return{id,hidden:false,dataset:{},checked:false,textContent:'',parentNode:null,focused:0,scrolled:0,
 focus(){this.focused++},scrollIntoView(){this.scrolled++},addEventListener(t,f){(L[t]=L[t]||[]).push(f)},
 fire(t){(L[t]||[]).forEach(f=>f({target:this}))},querySelector(){return null},insertBefore(n){n.parentNode=this;ids[n.id]=n}}}
const ids={},doc={documentElement:el('html'),getElementById(i){return ids[i]||null},createElement(){return el('')}};
const mode=process.argv[2];
['phone','home','back','settings','ask','q','answer','webq'].forEach(i=>ids[i]=el(i));
if(mode==='noask')delete ids.ask;else{ids.ask.parentNode=el('box');ids.ask.hidden=true}
const out={toggles:0};
const ctx={document:doc,location:{search:''},localStorage:{getItem(){return null},setItem(){},removeItem(){}},
 matchMedia(){return{matches:false}},addEventListener(){},scrollTo(){},Promise,setTimeout,vis:false,
 toggleVis(){out.toggles++;if(mode==='reject')return Promise.reject(new Error('down'));if(mode!=='refuse'){ctx.vis=true;ids.ask.hidden=false}return Promise.resolve()}};
ctx.window=ctx;vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
const hooks=()=>(ctx.oposHooks||[]).forEach(f=>f({helper:{engine:mode==='lan'?'Muse Glimmer, on a computer you own':'Llama 3.2 1B, on this phone'}}));
(async()=>{
 hooks();out.start=ids.phone.dataset.app||'';const w=ids['helper-where'];out.label0=w?w.textContent:null;
 ids.webq.checked=true;ids.webq.fire('change');out.label1=w?w.textContent:null;ids.webq.checked=false;ids.webq.fire('change');
 for(let i=0;i<3;i++)ctx.oposUI.openApp('helper');
 // Real render() runs the hooks only on a good reply; a failed toggle gets no later render, so recovery must not need one.
 for(let i=0;i<20;i++)await null;
 if(mode==='ok'||mode==='lan')hooks();
 await new Promise(r=>setTimeout(r,20));
 out.app=ids.phone.dataset.app||'';out.homeHidden=ids.home.hidden;
 out.qFocused=ids.q.focused;out.scrolled=ids.ask?ids.ask.scrolled:0;
 out.label2=w?w.textContent:null;out.answer=ids.answer.textContent;
 console.log(JSON.stringify(out));
})().catch(e=>console.log(JSON.stringify({error:String(e)})));
"""


class UXP07Helper(unittest.TestCase):
    def setUp(self):
        self.out = page("t" * 32, "n")

    def test_helper_entry_points_exist(self):
        self.assertIn('data-open="helper"', self.out)
        # The lock screen (UXP02) may not be merged yet; when it is, its Helper button must be there once.
        if 'id="lock"' in self.out:
            self.assertEqual(self.out.count('id="lock-ask"'), 1)
        for i in ("ask", "q", "answer"):
            self.assertEqual(self.out.count('id="%s"' % i), 1, i)

    def test_web_search_default_off(self):
        m = re.search(r"<input[^>]*id=\"webq\"[^>]*>", self.out)
        self.assertIsNotNone(m)
        self.assertNotIn("checked", m.group(0))

    def test_helper_offline_rules_answer(self):
        from opos import assist

        attempts = []

        def down(*a, **k):
            attempts.append(a[0])
            raise OSError("offline")

        # With transport explicitly offline, report no answer without naming a failed route.
        r = assist.answer("what is paused?", web=False, env={}, free=5000, context={}, opener=down)
        self.assertTrue(attempts)
        self.assertIsInstance(r, dict)
        self.assertIs(r["ok"], False)
        self.assertEqual((r["engine"], r["where"]), ("none", ""))
        self.assertIn("No model answered.", r["text"])
        self.assertIn("Calls, messages", r["text"])
        self.assertEqual(r["sources"], [])
        # Battery critical answers from rules without even invoking the offline transport.
        attempts.clear()
        r = assist.answer("what is paused?", web=False, env={}, free=5000,
                          context={"mode": "CRITICAL"}, opener=down)
        self.assertEqual(attempts, [])
        self.assertEqual((r["engine"], r["where"]), ("rules", "on this phone"))
        self.assertTrue(r["text"].startswith("Battery is critical"))

    def test_label_texts_present(self):
        js = (STATIC / "phone-b.js").read_text(encoding="utf-8")
        for t in (OFF, ON, LAN, "webq", "helper-where"):
            self.assertIn(t, js)
        self.assertIn("helper-where", self.out)
        for word in ("AI", "model", "governor", "assistant"):
            self.assertNotRegex(OFF + ON + LAN, r"\b%s\b" % word)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class UXP07HelperJS(unittest.TestCase):
    def run_js(self, mode):
        p = subprocess.run(["node", "-e", HARNESS, str(STATIC / "phone-b.js"), mode], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertNotIn("error", r)
        return r

    def test_one_tap_shows_helper_and_focuses_q(self):
        r = self.run_js("ok")
        self.assertEqual(r["start"], "home")
        self.assertEqual(r["toggles"], 1)  # three quick taps, one toggle
        self.assertEqual(r["app"], "helper")
        self.assertTrue(r["homeHidden"])
        self.assertEqual(r["qFocused"], 1)
        self.assertGreaterEqual(r["scrolled"], 1)

    def test_label_follows_checkbox_and_route(self):
        r = self.run_js("ok")
        self.assertEqual((r["label0"], r["label1"], r["label2"]), (OFF, ON, OFF))
        self.assertEqual(r["answer"], "")  # the label never writes into the answer
        self.assertEqual(self.run_js("lan")["label2"], LAN)

    def test_failed_toggle_falls_back_to_full_view(self):
        for mode in ("reject", "refuse"):
            with self.subTest(mode=mode):
                r = self.run_js(mode)
                self.assertEqual(r["toggles"], 1)
                self.assertEqual(r["app"], "")  # full view: the shell hides nothing
                self.assertTrue(r["homeHidden"])
                self.assertEqual(r["qFocused"], 0)

    def test_no_ask_is_fail_open(self):
        r = self.run_js("noask")
        self.assertIsNone(r["label0"])
        self.assertEqual(r["toggles"], 0)


if __name__ == "__main__":
    unittest.main()
