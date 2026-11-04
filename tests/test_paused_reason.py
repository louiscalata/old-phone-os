# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-task pause explanations and native disclosure controls."""
import json
from html.parser import HTMLParser
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from opos.advisor import GlimmerAdvisor
from opos.dashboard import Dashboard
from opos.governor import Priority, Task
from opos.router import Router
from opos.runtime import Runtime
from opos.web import Phone, page
from tests.test_uxr_acceptance import find_chrome


class PausedReasonState(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch("opos.web.prefs.load", side_effect=OSError),
                        mock.patch("opos.web.assist.free_mb", return_value=4096),
                        mock.patch("opos.web.self_rss_mb", return_value=12)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def reasons(self, phone):
        rows = phone.state()["battery"]["paused"]
        for row in rows:
            self.assertTrue(row["reason"].endswith("."))
            self.assertEqual(row["reason"].count("."), 1)
            self.assertNotRegex(row["reason"], r"\b(?:AI|model|router|governor)\b")
        return {row["name"]: row["reason"] for row in rows}

    def add_glimmer(self, phone, names=("email",)):
        ask = mock.Mock(return_value=json.dumps({"defer": names}))
        phone.runtime.governor.advisor = GlimmerAdvisor(
            Router(visible=True), route=object(), ask=ask, spawn=lambda fn: fn())
        phone.state()  # Fill the advisor cache; the next tick applies it.
        return ask

    def test_router_reasons_distinguish_task_cost_and_optional_threshold(self):
        reasons = self.reasons(Phone(visible=True))
        self.assertEqual(set(reasons), {"maps", "photos backup"})
        self.assertEqual(reasons["maps"], "Battery rules paused this to keep power for the next 4 hours.")
        self.assertEqual(reasons["photos backup"], "Battery rules paused this because usable battery is below 80%.")

    def test_optional_threshold_uses_health_not_raw_charge(self):
        phone = Phone(visible=True)
        phone.sim.level = 100
        reasons = self.reasons(phone)
        self.assertIn("usable battery is below 80%", reasons["photos backup"])
        phone.sim.health = 1
        self.assertEqual(self.reasons(phone), {})

    def test_hidden_helper_keeps_derived_battery_facts_private(self):
        phone = Phone()
        phone.sim.level = 30
        state = phone.state()
        self.assertNotIn("helper", state)
        self.assertNotIn("usable", json.dumps(state))
        reason = next(p["reason"] for p in state["battery"]["paused"] if p["name"] == "photos backup")
        self.assertEqual(reason, "Battery rules paused this to save power for calls and messages.")

    def test_hot_phone_explains_heat(self):
        phone = Phone()
        phone.sim.level, phone.sim.health, phone.sim.temp_c = 100, 1, 50
        reasons = self.reasons(phone)
        self.assertEqual(set(reasons), {"email", "maps", "photos backup"})
        self.assertEqual(set(reasons.values()), {"Battery rules paused this because the phone is hot."})

    def test_mixed_glimmer_and_router_pauses_keep_per_task_owner(self):
        phone = Phone(visible=True)
        ask = self.add_glimmer(phone)
        reasons = self.reasons(phone)
        self.assertEqual(reasons["email"], "Muse Glimmer paused this to save power for calls and messages.")
        self.assertTrue(reasons["maps"].startswith("Battery rules"))
        self.assertTrue(reasons["photos backup"].startswith("Battery rules"))
        ask.assert_called_once()  # Explanations themselves do not request inference.

    def test_dashboard_goal_pause_is_not_attributed_to_glimmer(self):
        phone = Phone(visible=True)
        phone.sim.level, phone.sim.health = 100, 1
        phone.dash.set_goal("18:00")
        self.add_glimmer(phone)
        reasons = self.reasons(phone)
        self.assertEqual(reasons["photos backup"], "Battery rules paused this to help the battery last until 6 PM.")
        self.assertTrue(reasons["email"].startswith("Muse Glimmer"))

    def test_worn_battery_reserve_explains_calls(self):
        phone = Phone(visible=True)
        self.add_glimmer(phone)
        phone.sim.level, phone.sim.health = 8, .4
        reasons = self.reasons(phone)
        self.assertTrue(reasons)
        self.assertEqual(set(reasons.values()), {"Battery rules paused this to keep a battery reserve for calls."})

    def test_measured_reserve_overrides_glimmer_attribution(self):
        phone = Phone(visible=True)
        phone.sim.level, phone.sim.health = 100, 1
        self.add_glimmer(phone)
        with mock.patch.object(phone.runtime.estimator, "rate", return_value=100):
            reasons = self.reasons(phone)
        self.assertEqual(set(reasons), {"email", "maps", "photos backup"})
        self.assertEqual(set(reasons.values()), {"Battery rules paused this to keep a battery reserve for calls."})

    def test_goal_waives_measured_reserve_without_losing_glimmer(self):
        phone = Phone(visible=True)
        phone.sim.level, phone.sim.health = 100, 1
        phone.dash.set_goal("18:00")
        self.add_glimmer(phone)
        with mock.patch.object(phone.runtime.estimator, "rate", return_value=100):
            reasons = self.reasons(phone)
        self.assertTrue(reasons["email"].startswith("Muse Glimmer"))
        self.assertNotIn("reserve", " ".join(reasons.values()))

    def test_charging_clears_rules_but_can_keep_an_extra_glimmer_pause(self):
        phone = Phone(visible=True)
        phone.charging = True
        self.assertEqual(self.reasons(phone), {})
        self.add_glimmer(phone)
        self.assertEqual(self.reasons(phone), {"email": "Muse Glimmer paused this to save power for calls and messages."})

    def test_undo_and_in_use_remove_the_row_and_reason(self):
        phone = Phone()
        row = next(row for row in phone.state()["battery"]["paused"] if row["name"] == "photos backup")
        phone.dash.undo(row["id"])
        self.assertNotIn("photos backup", self.reasons(phone))
        phone.dash.set_in_use("maps")
        self.assertEqual(self.reasons(phone), {})

    def test_quiet_redacted_name_still_gets_the_right_reason(self):
        phone = Phone()
        phone.runtime = Runtime(tasks=(Task("calls", Priority.ESSENTIAL, .5), Task("AI backup", Priority.OPTIONAL, 6)))
        phone.dash = Dashboard(phone.runtime.tasks)
        reasons = self.reasons(phone)
        self.assertEqual(set(reasons), {"... backup"})
        self.assertEqual(reasons["... backup"], "Battery rules paused this to save power for calls and messages.")


class _RowMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.text, self.attrs = [], [], {}

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs[tag] = dict(attrs)

    def handle_data(self, data):
        self.text.append(data)


class PausedReasonRender(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node not installed")
    def test_render_escapes_names_and_reasons_and_replaces_rows_on_undo(self):
        script = r"""
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8').split('const poly=')[0];
const list={children:[],set innerHTML(v){this.children=[]},querySelectorAll(){return []}},calls=[];
const state={battery:{paused:[{name:'maps <&"',id:'#3',reason:'Battery rules <img src=x onerror=bad>.'},{name:'photos backup',id:'#4',reason:'Battery rules paused this to save power.'}]}};
const context={G:()=>list,E:()=>({button:{},querySelector(){return this.button}}),
  esc:v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  api:async o=>{calls.push(o);state.battery.paused=state.battery.paused.filter(p=>p.id!==o.name);return state}};
list.appendChild=row=>list.children.push(row);
vm.createContext(context);vm.runInContext('function render(s){'+source+'}',context);
(async()=>{context.render(state);const before=list.children[0],html=before.innerHTML;
context.render(state);const stable=list.children[0]===before;await list.children[0].button.onclick();
const replaced=list.children[0]!==before;
process.stdout.write(JSON.stringify({html,stable,replaced,calls,remaining:list.children.map(r=>r.innerHTML)}));})();
"""
        source = Path(__file__).resolve().parents[1] / "src/opos/web_static/js-7.js"
        result = subprocess.run([shutil.which("node"), "-e", script, str(source)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        row = _RowMarkup()
        row.feed(output["html"])
        self.assertEqual(row.tags, ["details", "summary", "span", "p", "button"])
        self.assertNotIn("open", row.attrs["details"])
        self.assertEqual(row.text[:2], ['maps <&"', 'Battery rules <img src=x onerror=bad>.'])
        self.assertEqual(row.attrs["button"]["aria-label"], 'Turn maps <&" back on')
        self.assertTrue(output["stable"])
        self.assertTrue(output["replaced"])
        self.assertEqual(output["calls"], [{"action": "undo", "name": "#3"}])
        self.assertEqual(len(output["remaining"]), 1)
        self.assertIn("photos backup", output["remaining"][0])


# Browser uses a file page, a stubbed fetch, and DevTools pipes: no web server.
CDP = r"""
const fs=require('fs'),path=require('path'),{spawn}=require('child_process');
const [chrome,url,stateFile,profile]=process.argv.slice(2),S=JSON.parse(fs.readFileSync(stateFile,'utf8'));
const child=spawn(chrome,['--headless','--no-sandbox','--disable-gpu',...(process.platform==='darwin'?['--single-process']:[]),'--disable-background-networking','--remote-debugging-pipe','--user-data-dir='+profile,'--no-first-run','about:blank'],{stdio:['ignore','ignore','pipe','pipe','pipe']});
let seq=0,buf=Buffer.alloc(0),session,errors='';const pending=new Map();
child.stderr.on('data',d=>errors+=d);
child.on('exit',(code,signal)=>{for(const p of pending.values())p.reject(Error('browser exited '+(signal||code)+': '+errors.slice(-800)));pending.clear()});
child.stdio[4].on('data',d=>{buf=Buffer.concat([buf,d]);for(let i;(i=buf.indexOf(0))>=0;buf=buf.subarray(i+1)){const m=JSON.parse(buf.subarray(0,i)),p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}}});
function send(method,params={},sid=session){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});child.stdio[3].write(JSON.stringify({id,method,params,...(sid?{sessionId:sid}:{})})+'\0')})}
async function ev(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(expr){for(let i=0;i<100;i++){if(await ev(expr))return;await sleep(20)}throw Error('not ready: '+expr)}
function check(value,why){if(!value)throw Error(why)}
async function key(key,code,n){await send('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:n,text:key==='Enter'?'\r':key});await send('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:n});await sleep(30)}
const PRE=`window.__state=${JSON.stringify(S)};window.__calls=[];window.__csp=[];document.addEventListener('securitypolicyviolation',e=>__csp.push(e.violatedDirective));window.fetch=async function(u,o){const b=JSON.parse(o&&o.body||'{}');if(b.action){__calls.push(b);if(b.action==='undo')__state.battery.paused=__state.battery.paused.filter(p=>p.id!==b.name)}return new Response(JSON.stringify(__state),{status:200,headers:{'Content-Type':'application/json'}})};`;
(async()=>{const timer=setTimeout(()=>{child.kill('SIGKILL');process.exit(3)},30000);try{
const {targetId}=await send('Target.createTarget',{url:'about:blank'},null);({sessionId:session}=await send('Target.attachToTarget',{targetId,flatten:true},null));
await send('Page.enable');await send('Page.addScriptToEvaluateOnNewDocument',{source:PRE});
await send('Emulation.setDeviceMetricsOverride',{width:360,height:800,deviceScaleFactor:1,mobile:true});
await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
await send('Page.navigate',{url:url+'?lock=0'});
await wait("document.readyState==='complete'&&!!document.querySelector('#paused-list summary')&&!!window.oposUI");
await ev("oposUI.unlock();oposUI.openApp('home');openSheet()");
// Wait for the visible sheet to reach its final position, then let queued
// animation-frame focus work finish before focusing a summary.
await wait("getComputedStyle(document.querySelector('#sheet')).visibility==='visible'&&Math.abs(document.querySelector('#sheet').getBoundingClientRect().top)<1&&getComputedStyle(document.querySelector('#paused-list summary')).visibility==='visible'");
await ev("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))");
const selector="#paused-list summary",details="#paused-list details";
const initial=await ev(`(()=>{const s=document.querySelector('${selector}'),r=s.getBoundingClientRect(),b=document.querySelector('#paused-list button').getBoundingClientRect();s.focus();return {w:r.width,h:r.height,bw:b.width,bh:b.height,focus:document.activeElement===s,open:s.parentNode.open,name:s.textContent,label:document.querySelector('#paused-list span').textContent,hidden:s.closest('.paused-row').hidden}})()`);
check(initial.w>=48&&initial.h>=48&&initial.bw>=48&&initial.bh>=48,'targets smaller than 48px');
check(initial.focus&&!initial.open&&!initial.hidden&&initial.name===initial.label,'summary focus, name or row filter: '+JSON.stringify(initial));
await key('Enter','Enter',13);check(await ev(`document.querySelector('${details}').open`),'Enter did not open reason');
await key(' ','Space',32);check(!await ev(`document.querySelector('${details}').open`),'Space did not close reason');
const box=await ev(`(()=>{const r=document.querySelector('${selector}').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...box,radiusX:1,radiusY:1,force:1,id:1}]});await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await sleep(60);
check(await ev(`document.querySelector('${details}').open`),'touch did not open reason');
check(await ev("document.querySelector('#paused-list p').textContent===__state.battery.paused[0].reason"),'wrong inline reason');
await ev("window.__pausedFocus=document.querySelector('#paused-list button');__pausedFocus.focus();render(__state)");
check(await ev("document.activeElement===__pausedFocus&&document.querySelector('#paused-list button')===__pausedFocus"),'the 1-second re-render replaced a focused row');
check(await ev(`document.querySelector('${details}').open`),'the 1-second re-render closed an open reason');
await ev(`__state.battery.paused[0].reason='<img src=x onerror="window.__injected=1">';render(__state)`);
check(await ev("!document.querySelector('#paused-list img')&&!window.__injected&&document.querySelector('#paused-list p').textContent===__state.battery.paused[0].reason"),'reason not escaped');
await ev("document.querySelector('#paused-list button').click()");await wait("__calls.some(b=>b.action==='undo')");
check(await ev("__calls.find(b=>b.action==='undo').name==='#3'&&!Array.from(document.querySelectorAll('#paused-list span')).some(e=>e.textContent==='maps')"),'Undo changed or row persisted');
check(await ev('__csp.length===0'),'CSP violation');process.stdout.write('native disclosure checks passed\n');
}finally{clearTimeout(timer);child.kill('SIGKILL')}})().catch(e=>{process.stderr.write(String(e.stack||e)+'\n');child.kill('SIGKILL');process.exitCode=1});
"""


class PausedReasonBrowser(unittest.TestCase):
    def test_native_keyboard_touch_rerender_escape_and_undo(self):
        chrome, node = find_chrome(), shutil.which("node")
        if not chrome or not node:
            self.skipTest("no headless Chromium or Node")
        with tempfile.TemporaryDirectory(prefix="paused-reason-") as directory:
            root = Path(directory)
            nonce = "paused-reason-test"
            policy = "default-src 'none'; script-src 'nonce-" + nonce + "'; style-src 'unsafe-inline'; connect-src 'none'"
            html = page("t" * 43, nonce, demo=True).replace("<head>", '<head><meta http-equiv="Content-Security-Policy" content="' + policy + '">', 1)
            (root / "page.html").write_text(html, encoding="utf-8")
            with mock.patch("opos.web.prefs.load", side_effect=OSError):
                state = Phone().state()
            (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (root / "driver.js").write_text(CDP, encoding="utf-8")
            result = subprocess.run([node, str(root / "driver.js"), chrome, (root / "page.html").as_uri(), str(root / "state.json"), str(root / "profile")], capture_output=True, text=True, timeout=40, env=dict(os.environ, TMPDIR=directory))
            if "bootstrap_check_in" in result.stderr and "Permission denied" in result.stderr:
                self.skipTest("sandbox denies Chromium MachPortRendezvousServer launch")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("native disclosure checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
