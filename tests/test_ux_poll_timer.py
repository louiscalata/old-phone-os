# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from opos.web import page


# These are source checks on the assembled page script, not browser execution.
# The optional stub check runs js-8.js under node with fake timers and DOM.
JS8 = Path(__file__).resolve().parent.parent / "src" / "opos" / "web_static" / "js-8.js"


def script():
    body = page()
    # The core runtime owns the initial request and poll timer; extensions have separate scripts.
    m = re.search(r"<script>(.*?)</script>", body, re.S)
    return m.group(1)


class TestPollTimerSource(unittest.TestCase):
    def setUp(self):
        self.js = script()

    def test_tick_uses_timeout_chain(self):
        self.assertNotIn("setInterval(async", self.js)
        # helperCall keeps its own setInterval(tick,1000) progress timer.
        self.assertIsNone(re.search(r"setInterval\([^;]*action:'tick'", self.js))
        self.assertIn("setTimeout(pollTick,", self.js)
        self.assertIn("armPoll(pollMs)", self.js)

    def test_tick_payload_and_running_guard(self):
        tick = re.search(r"async function pollTick\(\)\{.*?\n", self.js).group(0)
        self.assertIn("if(running)", tick)
        # real_s is the real time since the previous poll; the server caps it at 5 s.
        self.assertIn("real_s=(n-pollAt)/1000;pollAt=n;", tick)
        self.assertIn("api({action:'tick',real_s})", tick)
        self.assertIn("finally{", tick)
        self.assertLess(tick.find("await render(await api("), tick.find("finally{"))

    def test_poll_ms_read_after_render_guards(self):
        self.assertIn("let pollMs=1000", self.js)
        self.assertIn("pollMs=s.poll_ms||1000", self.js)
        wrap = re.search(r"render=async s=>\{.*?\n", self.js).group(0)
        self.assertLess(wrap.find("await renderShown(s)"), wrap.find("if(s&&!s.error){"))
        self.assertLess(wrap.find("if(s&&!s.error){"), wrap.find("pollMs=s.poll_ms"))

    def test_one_pending_timer_variable(self):
        self.assertEqual(self.js.count("setTimeout(pollTick"), 1)
        self.assertIn("clearTimeout(pollTimer)", self.js)
        self.assertEqual(len(re.findall(r"\bpollTimer=setTimeout\(", self.js)), 1)

    def test_poll_outcome_goes_to_the_page_not_to_an_error_render(self):
        # W1-02: a request that fails no longer renders an error line (hidden below the screen's edge). The page is
        # told (oposPoll), counts the misses, and draws its own banner after two (tests/test_uxp10_reliable.py).
        tick = re.search(r"async function pollTick\(\)\{.*?\n", self.js).group(0)
        self.assertNotIn("renderShown({error:", self.js)
        self.assertIn("oposPoll(1)}catch(e){oposPoll(0)}finally{", tick)
        self.assertLess(tick.find("await render(await fetchState())"), tick.find("oposPoll(1)"))
        self.assertIn("var oposPoll=()=>0;", self.js, "a no-op until phone-a.js installs the counter")

    def test_a_hung_state_request_gives_up_after_eight_seconds(self):
        fetch_state = re.search(r"function fetchState\(\)\{.*?\n", self.js).group(0)
        self.assertIn("signal:AbortSignal.timeout?.(8000)", fetch_state)

    def test_initial_load_preserved(self):
        self.assertIn("fetch('/api/state',", self.js)
        self.assertIn("Could not reach Old Phone OS Interface. Reload the page in a few seconds.", self.js)
        self.assertEqual(self.js.count("fetch('/api/state',"), 1)

    def test_shell_retry_is_separate_and_conditional(self):
        scripts = re.findall(r"<script>(.*?)</script>", page(), re.S)
        retries = [js for js in scripts[1:] if "fetch('/api/state'," in js]
        self.assertEqual(len(retries), 1)
        retry = retries[0]
        self.assertEqual(retry.count("fetch('/api/state',"), 1)
        self.assertIn("if(early)render(early);", retry)
        self.assertIn("else if(document.getElementById('screen').textContent)", retry)
        self.assertIn("},{once:true});", retry)


STUB = r"""
let now=0,timers=[],nextId=1,calls=0,stateReads=0,states=[],notices=[],fail=false,replies=[];
globalThis.Date={now:()=>now};
globalThis.setTimeout=(f,ms)=>{const id=nextId++;timers.push({id,f,at:now+ms});return id};
globalThis.clearTimeout=id=>{timers=timers.filter(t=>t.id!==id)};
const el=()=>({onclick:null,oninput:null,onchange:null,addEventListener(){},textContent:'',value:'',dataset:{}});
globalThis.document={getElementById:()=>el(),querySelector:()=>({content:'t'.repeat(43)}),querySelectorAll:()=>[]};
let polls=[];
globalThis.fetch=(url,options)=>{stateReads++;if(url!=='/api/state'||options.headers['X-OPOS-Token']!=='t'.repeat(43))throw new Error('bad state request');return stateReads===1?new Promise(()=>{}):Promise.resolve({json:async()=>({helper:{paused_by_glimmer:['email']}})})};
var running=true,vis=false;
var api=async o=>{calls++;if(fail)throw new Error('down');return replies.shift()||{poll_ms:5000}};
var render=async s=>{if(s&&s.error)notices.push(s.error);else if(s?.helper)states.push(s.helper)};
function go(){} function toggleSim(){}
"""

DRIVER = r"""
const flush=()=>new Promise(r=>setImmediate(r));
oposPoll=ok=>polls.push(ok);
async function advance(ms){const end=now+ms;for(;;){timers.sort((a,b)=>a.at-b.at);const t=timers[0];if(!t||t.at>end)break;timers.shift();now=t.at;t.f();for(let i=0;i<5;i++)await flush()}now=end}
(async()=>{
 const out={};
 out.start=timers.length;
 await advance(1000); out.afterFirst={calls,pending:timers.length,due:timers[0].at-now};
 await render({poll_ms:1000}); out.rearm={pending:timers.length,due:timers[0].at-now};
 fail=true; await advance(1000); out.fail={notices:notices.length,pending:timers.length,polls:polls.slice()};
 fail=false; running=false; const c=calls; await advance(1000); out.stopped={calls:calls-c,pending:timers.length,stateReads,states};
 console.log(JSON.stringify(out));
})();
"""


@unittest.skipUnless(shutil.which("node"), "node missing")
class TestPollTimerStub(unittest.TestCase):
    def test_chain_behaviour(self):
        code = STUB + JS8.read_text(encoding="utf-8") + DRIVER
        proc = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(out["start"], 1)
        self.assertEqual(out["afterFirst"], {"calls": 1, "pending": 1, "due": 5000})
        self.assertEqual(out["rearm"], {"pending": 1, "due": 1000})
        # one good poll, then the failing one: the page is told "not ok" and no error is rendered
        self.assertEqual(out["fail"], {"notices": 0, "pending": 1, "polls": [1, 0]})
        self.assertEqual(out["stopped"], {"calls": 0, "pending": 1, "stateReads": 2, "states": [{"paused_by_glimmer": ["email"]}]})


if __name__ == "__main__":
    unittest.main()
