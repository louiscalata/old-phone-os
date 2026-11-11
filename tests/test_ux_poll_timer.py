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
        self.assertIn("oposPoll(s&&!s.error?1:0)}}catch(e){running||oposPoll(0)}finally{", tick)
        self.assertLess(tick.find("await render(s)"), tick.find("oposPoll(s&&!s.error?1:0)"))
        self.assertIn("var oposPoll=()=>0;", self.js, "a no-op until phone-a.js installs the counter")

    def test_a_simulation_tick_is_counted_once_by_api_not_again_by_the_poll(self):
        # api() (phone-a.js) already tells the page how a tap or a tick went; counting the tick's failure again here made
        # one lost request look like two misses, so the banner came after one failed poll whenever the simulation ran.
        tick = re.search(r"async function pollTick\(\)\{.*?\n", self.js).group(0)
        self.assertEqual(tick.count("oposPoll("), 2)
        self.assertLess(tick.find("else{"), tick.find("oposPoll(s&&!s.error?1:0)"), "only a state poll reports success itself")
        self.assertIn("catch(e){running||oposPoll(0)}", tick)

    def test_an_unchanged_state_is_not_drawn_again(self):
        # W1-09: the poll skipped a render whose state is what was last drawn (a11y-cost-05 (a)).
        self.assertIn("seen='';await renderShown(s);if(s&&!s.error){seen=stateKey(s);", self.js, "a render, or a failed one, always sets it")
        self.assertIn("const stateKey=s=>JSON.stringify(s,(k,v)=>k==='lean'?0:v)+navigator.onLine;", self.js)
        self.assertIn("if(!s||s.error||stateKey(s)!==seen)await render(s)", self.js)
        self.assertIn("['load','click','keydown','input'].forEach(t=>addEventListener(t,()=>seen='',1));", self.js)

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
        self.assertIn("else if(G('screen').textContent)", retry)
        self.assertIn("},{once:true});", retry)


STUB = r"""
let now=0,timers=[],nextId=1,calls=0,stateReads=0,states=[],notices=[],fail=false,failState=false,replies=[],listeners={},answer=null;
globalThis.Date={now:()=>now};
globalThis.setTimeout=(f,ms)=>{const id=nextId++;timers.push({id,f,at:now+ms});return id};
globalThis.clearTimeout=id=>{timers=timers.filter(t=>t.id!==id)};
globalThis.addEventListener=(t,f,capture)=>{(listeners[t]=listeners[t]||[]).push([f,capture])};
Object.defineProperty(globalThis,'navigator',{value:{onLine:true},configurable:true,writable:true});
const el=()=>({onclick:null,oninput:null,onchange:null,addEventListener(){},textContent:'',value:'',dataset:{}});
globalThis.document={getElementById:()=>el(),querySelector:()=>({content:'t'.repeat(43)}),querySelectorAll:()=>[]};
let polls=[];
globalThis.fetch=(url,options)=>{stateReads++;if(url!=='/api/state'||options.headers['X-OPOS-Token']!=='t'.repeat(43))throw new Error('bad state request');
 if(failState)return Promise.reject(new TypeError('down'));return stateReads===1?new Promise(()=>{}):Promise.resolve({json:async()=>answer||({helper:{paused_by_glimmer:['email']}})})};
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
 fail=false; running=false; const c=calls; await advance(1000); out.stopped={calls:calls-c,pending:timers.length,stateReads,states,polls:polls.slice()};
 failState=true; await advance(1000); out.stateDown={notices:notices.length,polls:polls.slice(),pending:timers.length};
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
        # a simulation tick that fails is api()'s to report (phone-a.js), so the poll adds nothing: no error is rendered and it is not counted twice
        self.assertEqual(out["fail"], {"notices": 0, "pending": 1, "polls": []})
        # a state poll reports its own outcome: good once, then, with the request lost, not ok once, and still no error line
        self.assertEqual(out["stopped"], {"calls": 0, "pending": 1, "stateReads": 2, "states": [{"paused_by_glimmer": ["email"]}], "polls": [1]})
        self.assertEqual(out["stateDown"], {"notices": 0, "polls": [1, 0], "pending": 1})


SKIP_STUB = r"""
let now=0,timers=[],nextId=1,reads=0,drawn=[],answer={a:1,lean:{opos_mb:50,free_mb:900}},down=false,polls=[],listeners={};
globalThis.Date={now:()=>now};
globalThis.setTimeout=(f,ms)=>{const id=nextId++;timers.push({id,f,at:now+ms});return id};
globalThis.clearTimeout=id=>{timers=timers.filter(t=>t.id!==id)};
globalThis.addEventListener=(t,f,capture)=>{(listeners[t]=listeners[t]||[]).push(f)};
Object.defineProperty(globalThis,'navigator',{value:{onLine:true},configurable:true,writable:true});
const el=()=>({onclick:null,oninput:null,onchange:null,addEventListener(){},textContent:'',value:'',dataset:{}});
globalThis.document={getElementById:()=>el(),querySelector:()=>({content:'t'.repeat(43)}),querySelectorAll:()=>[]};
globalThis.fetch=()=>{reads++;return down?Promise.reject(new TypeError('down')):Promise.resolve({json:async()=>JSON.parse(JSON.stringify(answer))})};
var running=false,vis=false;
var api=async o=>({});
var render=async s=>{drawn.push(s&&s.error?'error:'+s.error:JSON.stringify(s&&s.a))};
function go(){} function toggleSim(){}
"""

SKIP_DRIVER = r"""
const flush=()=>new Promise(r=>setImmediate(r));
oposPoll=ok=>polls.push(ok);
async function advance(ms){const end=now+ms;for(;;){timers.sort((a,b)=>a.at-b.at);const t=timers[0];if(!t||t.at>end)break;timers.shift();now=t.at;t.f();for(let i=0;i<5;i++)await flush()}now=end}
const fire=t=>listeners[t].forEach(f=>f({}));
(async()=>{
 const out={};
 await flush(); await flush(); out.boot=drawn.length;
 await advance(1000); out.firstPollBeforeLoad=drawn.length;
 await advance(1000); out.same=drawn.length;
 answer={a:1,lean:{opos_mb:51,free_mb:123}}; await advance(1000); out.leanOnly=drawn.length;
 answer={a:2,lean:{opos_mb:51,free_mb:123}}; await advance(1000); out.changed=drawn.length;
 await advance(1000); out.changedThenSame=drawn.length;
 navigator.onLine=false; await advance(1000); out.offline=drawn.length;
 await advance(1000); out.offlineSame=drawn.length;
 for (const t of ['click','keydown','input']) { const before=drawn.length; fire(t); await advance(1000); out[t]=drawn.length-before; await advance(1000); out[t+'Then']=drawn.length-before; }
 const b=drawn.length; fire('load'); await advance(1000); out.load=drawn.length-b;
 await render({error:'offline'}); await advance(1000); out.afterErrorRender=drawn.slice(-2);
 answer={error:'forbidden'}; const e=drawn.length; await advance(1000); await advance(1000); out.errorsAlwaysDrawn=drawn.length-e;
 answer={a:2}; down=true; const d=drawn.length; await advance(1000); out.down={drawn:drawn.length-d,polls:polls.slice(-1)};
 console.log(JSON.stringify(out));
})();
"""


@unittest.skipUnless(shutil.which("node"), "node missing")
class TestPollSkipsAnUnchangedState(unittest.TestCase):
    """W1-09 / a11y-cost-05 (a): a poll whose state is what was last drawn draws nothing (about 60 DOM writes per poll before)."""
    @classmethod
    def setUpClass(cls):
        code = SKIP_STUB + JS8.read_text(encoding="utf-8") + SKIP_DRIVER
        proc = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        cls.out = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_the_first_poll_before_the_load_event_draws_again_and_then_the_same_state_does_not(self):
        o = self.out
        self.assertEqual(o["boot"], 1)
        self.assertEqual(o["firstPollBeforeLoad"], 1, "the stub never fires 'load', so the state of the first draw is already what was drawn")
        self.assertEqual(o["same"], 1)

    def test_a_change_is_drawn_but_the_memory_figures_alone_are_not_a_change(self):
        o = self.out
        self.assertEqual(o["leanOnly"], 1)
        self.assertEqual(o["changed"], 2)
        self.assertEqual(o["changedThenSame"], 2)

    def test_going_offline_is_a_change_because_the_status_bar_shows_it(self):
        self.assertEqual(self.out["offline"], 3)
        self.assertEqual(self.out["offlineSame"], 3)

    def test_any_tap_key_or_input_draws_the_next_poll_again_so_local_text_is_put_back_as_before(self):
        # a notice the page wrote itself ("Type the incoming text first.") used to be cleared by the next poll
        for t in ("click", "keydown", "input"):
            self.assertEqual((self.out[t], self.out[t + "Then"]), (1, 1), t)
        self.assertEqual(self.out["load"], 1, "scripts that load after js-8.js may have missed the first draw: 'load' re-arms it")

    def test_an_error_render_and_an_error_answer_are_always_drawn(self):
        self.assertEqual(self.out["afterErrorRender"], ["error:offline", "2"])
        self.assertEqual(self.out["errorsAlwaysDrawn"], 2)

    def test_a_lost_request_draws_nothing_and_counts_one_miss(self):
        self.assertEqual(self.out["down"], {"drawn": 0, "polls": [0]})


if __name__ == "__main__":
    unittest.main()
