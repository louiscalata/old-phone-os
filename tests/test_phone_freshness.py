# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import shutil
import subprocess
import unittest

STATIC = Path(__file__).resolve().parents[1] / 'src/opos/web_static'
NODE = shutil.which('node')

# Run the actual poll scheduler and freshness hook together, with no browser or socket.
HARNESS = r"""
let now=1700000000000,timers=[],nextId=1,queue=[],requests=[],notices=[],display='';
globalThis.Date={now:()=>now};
globalThis.performance={now:()=>now};
globalThis.setTimeout=(f,ms)=>{let id=nextId++;timers.push({id,f,at:now+ms});return id};
globalThis.clearTimeout=id=>{timers=timers.filter(t=>t.id!==id)};
globalThis.setInterval=()=>{};
globalThis.addEventListener=()=>{};
globalThis.navigator={onLine:true};
globalThis.sessionStorage={};
globalThis.location={reload(){}};
globalThis.AbortSignal={timeout:ms=>({timeout:ms})};
const elements={};
function element(id){return elements[id]||(elements[id]={hidden:true,dataset:{},textContent:'',value:'',
 addEventListener(){},toggleAttribute(n,on){if(n==='hidden')this.hidden=!!on}})}
let stale=false;
globalThis.document={getElementById:element,querySelector:()=>({content:'test-token'}),querySelectorAll:()=>[],
 body:{toggleAttribute(n,on){if(n==='data-stale')stale=!!on}}};
globalThis.window=globalThis;
globalThis.fetch=async(url,options)=>{requests.push({url,options});let r=queue.shift();
 if(!r)throw Error('no response queued');if(r.reject)throw Error('offline');
 return {status:r.status,ok:r.status>=200&&r.status<300,json:async()=>r.payload}};
var running=false,vis=false;
var api=async()=>({});
function go(){} function toggleSim(){} function showNotice(s){notices.push(s)}
"""


def program(driver):
    js4 = (STATIC / 'js-4.js').read_text()
    render = js4[js4.index('async function render(s){'):js4.index('running=!!s.running')]
    render += "display=s.status; (window.oposHooks||[]).forEach(f=>f(s));}\n"
    phone = (STATIC / 'phone-a.js').read_text()
    marker = phone.index("var retry=G('reload-shell'),m=0;")
    hook = phone[phone.rfind('(function(){', 0, marker):phone.index('})();', marker) + len('})();')]
    return HARNESS + js4[js4.index('function healReload('):js4.index('const sync=')] + render + "\nqueue.push({status:200,payload:{status:'old',poll_ms:1000}});\n" + (STATIC / 'js-8.js').read_text() + hook + driver


DRIVER_SETUP = r"""
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(setImmediate)};
async function step(reply){queue.push(reply);timers.sort((a,b)=>a.at-b.at);let i=timers.findIndex(t=>t.f===pollTick),t=timers.splice(i,1)[0];
 if(i<0||!t)throw Error('no poll scheduled');now=t.at;t.f();await flush()}
function snapshot(){return {hidden:element('reload-shell').hidden,stale,display,notice:element('notice').textContent}}
"""


@unittest.skipUnless(NODE, 'node missing')
class PhoneFreshness(unittest.TestCase):
    def run_driver(self, driver):
        proc = subprocess.run([NODE, '--unhandled-rejections=strict', '-e', program(DRIVER_SETUP + driver)],
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_poll_contract_and_transport_failures_recover(self):
        out = self.run_driver(r"""
(async()=>{await flush();let out={boot:snapshot(),request:requests[0]};
 await step({reject:true});out.one=snapshot();await step({reject:true});out.two=snapshot();
 await step({status:200,payload:{status:'new',poll_ms:3000}});out.good=snapshot();
 out.next=timers[0].at-now;console.log(JSON.stringify(out))})().catch(e=>{console.error(e);process.exitCode=1});
""")
        self.assertEqual(out['boot'], {'hidden': True, 'stale': False, 'display': 'old', 'notice': ''})
        self.assertEqual(out['request'], {'url': '/api/state', 'options': {'headers': {'X-OPOS-Token': 'test-token'}, 'signal': {'timeout': 8000}}})
        self.assertEqual(out['one']['hidden'], True)
        self.assertEqual(out['one']['stale'], False)
        self.assertEqual(out['two'], {'hidden': False, 'stale': True, 'display': 'old', 'notice': 'Not updating. Values shown may be old. Tap Reload.'})
        self.assertEqual(out['good']['hidden'], True)
        self.assertEqual(out['good']['stale'], False)
        self.assertEqual(out['good']['display'], 'new')
        self.assertEqual(out['next'], 3000)

    def test_fetch_state_returns_the_original_json_payload(self):
        out = self.run_driver(r"""
(async()=>{await flush();let payload={status:'ready',poll_ms:5000,extra:{n:7}};
 queue.push({status:200,payload});console.log(JSON.stringify(await fetchState()))})().catch(e=>{console.error(e);process.exitCode=1});
""")
        self.assertEqual(out, {'status': 'ready', 'poll_ms': 5000, 'extra': {'n': 7}})

    def test_http_errors_count_as_misses_until_a_snapshot_recovers(self):
        out = self.run_driver(r"""
(async()=>{await flush();let out={};
 await step({status:403,payload:{error:'forbidden'}});out.forbidden=snapshot();
 out.heal=timers.filter(t=>t.f===healReload).map(t=>t.at-now);
 await step({status:500,payload:{error:'unavailable'}});out.failed=snapshot();
 await step({status:500,payload:{status:'wrong',poll_ms:10}});out.badStatus=snapshot();
 await step({status:200,payload:{status:'new',poll_ms:3000}});out.recovered=snapshot();
 console.log(JSON.stringify(out))})().catch(e=>{console.error(e);process.exitCode=1});
""")
        self.assertEqual(out['forbidden'], {'hidden': True, 'stale': False, 'display': 'old', 'notice': 'Restarting the screen. One moment.'})
        self.assertEqual(out['heal'], [500])
        self.assertEqual(out['failed'], {'hidden': False, 'stale': True, 'display': 'old', 'notice': 'Not updating. Values shown may be old. Tap Reload.'})
        self.assertEqual(out['badStatus']['hidden'], False)
        self.assertEqual(out['badStatus']['stale'], True)
        self.assertEqual(out['badStatus']['display'], 'old')
        self.assertEqual(out['recovered']['hidden'], True)
        self.assertEqual(out['recovered']['stale'], False)
        self.assertEqual(out['recovered']['display'], 'new')

    def test_json_error_with_success_status_still_counts_as_a_miss(self):
        out = self.run_driver(r"""
(async()=>{await flush();await step({reject:true});
 await step({status:200,payload:{error:'unavailable'}});console.log(JSON.stringify(snapshot()))
 })().catch(e=>{console.error(e);process.exitCode=1});
""")
        self.assertEqual(out, {'hidden': False, 'stale': True, 'display': 'old', 'notice': 'Not updating. Values shown may be old. Tap Reload.'})

    def test_unusable_payloads_do_not_clear_stale_and_identical_snapshot_does(self):
        out = self.run_driver(r"""
(async()=>{await flush();let out={};
 await step({status:500,payload:{status:'wrong',poll_ms:10}});
 await step({status:200,payload:null});out.stale=snapshot();
 await step({status:403,payload:{error:'forbidden'}});out.forbidden=snapshot();
 await step({status:200,payload:{status:'old',poll_ms:1000}});out.recovered=snapshot();
 console.log(JSON.stringify(out))})().catch(e=>{console.error(e);process.exitCode=1});
""")
        self.assertEqual(out['stale'], {'hidden': False, 'stale': True, 'display': 'old', 'notice': 'Not updating. Values shown may be old. Tap Reload.'})
        self.assertEqual(out['forbidden']['hidden'], False)
        self.assertEqual(out['forbidden']['stale'], True)
        self.assertEqual(out['forbidden']['display'], 'old')
        self.assertEqual(out['recovered']['hidden'], True)
        self.assertEqual(out['recovered']['stale'], False)
        self.assertEqual(out['recovered']['display'], 'old')
