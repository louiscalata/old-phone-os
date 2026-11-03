# SPDX-License-Identifier: GPL-3.0-or-later
"""Night dimming follows the displayed clock without replacing saved brightness.

In the demo the status bar reads "Demo time HH:MM" (tests/test_demo_clock_label.py);
dimming reads the simulated clock and leaves that label alone.
"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"

# Execute the real preference path, state rendering and status-bar clock. The
# observer queue models delivery after a task, including repeated text writes.
HARNESS = r"""
const fs=require('fs'),vm=require('vm'),path=require('path');
const read=name=>fs.readFileSync(path.join(process.argv[1],name),'utf8');
const bar=read('js-3.js'),state=read('js-6.js'),phone=read('phone-a.js');
const start=phone.indexOf("(function(){\nvar time=G('sb-time');");
if(start<0)throw Error('Status-bar clock missing');
const clock=phone.slice(start,phone.indexOf('})();',start)+5);
function run(o={}){
  const store={...o.store},ids={},queue=new Set(),timers=[];
  let wall=o.wall||'12:00',writes=0;
  function el(id){
    if(ids[id])return ids[id];
    let text='',value='',checked=false;
    const e=ids[id]={id,style:{},dataset:{},attrs:{},listeners:{},observers:[],
      addEventListener(t,f){(this.listeners[t]||=[]).push(f)},
      setAttribute(k,v){this.attrs[k]=String(v)},
      removeAttribute(k){delete this.attrs[k];if(k==='hidden')this.hidden=false},
      dispatchEvent(e){for(const f of this.listeners[e.type]||[])f(e)},
      focus(){},get value(){return value},set value(v){value=String(v)},
      get checked(){return checked},set checked(v){checked=!!v},
      get textContent(){return text},set textContent(v){text=String(v);for(const ob of this.observers)queue.add(ob)}};
    return e;
  }
  Object.assign(el('bright'),{type:'range',value:'100',min:'40',max:'100'});
  Object.assign(el('vol'),{type:'range',value:'60',min:'0',max:'100'});
  Object.assign(el('night'),{type:'checkbox',checked:false});
  el('topbar').value='b';
  el('demo').hidden=!o.demo;
  if(o.demo)el('demo').dataset.demo='1';
  const root={style:{}};
  const win={oposHooks:[],addEventListener(){}};
  const c={document:{getElementById:el,documentElement:root,addEventListener(){},activeElement:null},
    window:win,navigator:{onLine:true},location:{search:o.demoQuery?'?demo=1':''},URLSearchParams,
    G:el,meterEl:el('meter'),barM:undefined,barE:undefined,
    handleEl:el('handle'),titleEl:el('title'),timeEl:el('time'),gripEl:el('grip'),scrimEl:el('scrim'),
    closeSheet(){},Date:class{getHours(){return +wall.slice(0,2)}getMinutes(){return +wall.slice(3)}},
    setInterval(f,ms){timers.push({f,ms})},
    MutationObserver:class{constructor(f){this.f=f}observe(e,options){if(!options.childList)throw Error('Clock text not observed');e.observers.push(this)}},
    localStorage:{getItem(k){if(o.blocked)throw Error('denied');return store[k]??null},
      setItem(k,v){if(o.blocked)throw Error('denied');store[k]=String(v);writes++}}};
  vm.createContext(c);
  vm.runInContext(read('js-1.js').split('\n').find(line=>line.startsWith('if(new URLSearchParams')),c);
  vm.runInContext(bar,c);
  vm.runInContext('function renderState(s){'+state+'}}',c);
  vm.runInContext(phone.split('\n').find(line=>line.startsWith('var demoTime=')),c);
  vm.runInContext(clock,c);
  function flush(){let n=0;while(queue.size){if(++n>10)throw Error('Clock observer did not settle');const batch=[...queue];queue.clear();for(const ob of batch)ob.f()}}
  function snapshot(){return {time:el('sb-time').textContent,filter:root.style.filter,
    bright:el('bright').value,night:el('night').checked,width:el('meter-fill').style.width,
    label:el('meter').attrs['aria-label'],store:{...store},writes}}
  function render(clockValue){c.renderState({clock:clockValue,battery:{percent:61,meter:{width:61,label:'61 percent'}}});
    for(const f of win.oposHooks)f({clock:clockValue,battery:{percent:61}});flush();return snapshot()}
  function tick(t){wall=t;for(const timer of timers)timer.f();flush();return snapshot()}
  function change(id,v){const e=el(id);if(e.type==='checkbox')e.checked=v;else e.value=v;
    e.dispatchEvent({type:'input',target:e});e.dispatchEvent({type:'change',target:e});flush();return snapshot()}
  flush();
  return {snapshot,render,tick,change,timers};
}
const saved={opos_bright:'85',opos_vol:'60',opos_bar:'r'},on={...saved,opos_night:'true'},out={};
let r=run({store:saved,wall:'23:00'});out.default=r.snapshot();
r=run({store:on,wall:'21:59'});out.boundaries=[r.snapshot()];
for(const t of ['22:00','23:59','00:00','06:59','07:00'])out.boundaries.push(r.tick(t));
out.timerPeriods=r.timers.map(t=>t.ms);
r=run({store:saved,wall:'23:00'});out.enabled=r.change('night',true);
out.reloadedOn=run({store:out.enabled.store,wall:'23:00'}).snapshot();
out.disabled=r.change('night',false);
out.reloadedOff=run({store:out.disabled.store,wall:'23:00'}).snapshot();
out.invalid=['false','1','garbage',''].map(v=>run({store:{...saved,opos_night:v},wall:'23:00'}).snapshot());
r=run({store:on,wall:'23:00'});out.changedAtNight=r.change('bright','90');out.changedAtDawn=r.tick('07:00');
r=run({store:on,wall:'23:00'});r.render('12:00');out.volume=r.change('topbar','v');out.battery=r.change('topbar','b');out.brightness=r.change('topbar','r');
r=run({store:on,wall:'12:00'});out.realIgnoresSimulation=r.render('23:00');
r=run({store:on,wall:'12:00',demo:true});out.demoNight=r.render('22:00');
out.demoWallTick=r.tick('07:00');out.demoPreDawn=r.render('06:59');out.demoDawn=r.render('07:00');
r=run({store:on,wall:'12:00',demoQuery:true});out.demoQueryNight=r.render('22:00');
r=run({store:on,wall:'23:00',blocked:true});out.blockedDefault=r.snapshot();
out.blockedEnabled=r.change('night',true);out.blockedChanged=r.change('bright','90');out.blockedDawn=r.tick('07:00');
r=run({store:{opos_night:'true',opos_bar:'r'},wall:'06:59'});out.fullAtNight=r.snapshot();out.fullAtDawn=r.tick('07:00');
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node missing")
class NightDimRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run(["node", "-e", HARNESS, str(STATIC)],
                              capture_output=True, text=True, timeout=20)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.result = json.loads(proc.stdout)

    def assert_brightness(self, state, effective, saved="85"):
        expected = "" if effective == 100 else "brightness(%s)" % (effective / 100)
        self.assertEqual(state["filter"], expected)
        self.assertEqual(state["bright"], saved)
        self.assertEqual(state["width"], "%d%%" % effective)
        self.assertEqual(state["label"], "Brightness %d percent, pull down for details" % effective)

    def test_switch_defaults_off_even_at_night(self):
        state = self.result["default"]
        self.assertFalse(state["night"])
        self.assert_brightness(state, 85)
        self.assertNotIn("opos_night", state["store"])

    def test_clock_ticks_cross_both_boundaries_without_state_render(self):
        times = ["21:59", "22:00", "23:59", "00:00", "06:59", "07:00"]
        for state, time, effective in zip(self.result["boundaries"], times, [85, 40, 40, 40, 40, 85]):
            with self.subTest(time=time):
                self.assertEqual(state["time"], time)
                self.assert_brightness(state, effective)
                self.assertEqual(state["store"]["opos_bright"], "85")
                self.assertEqual(state["writes"], 0)
        self.assertIn(15000, self.result["timerPeriods"])

    def test_switch_persists_on_and_off_and_restores_on_reload(self):
        for key in ("enabled", "reloadedOn"):
            self.assertTrue(self.result[key]["night"])
            self.assertEqual(self.result[key]["store"]["opos_night"], "true")
            self.assert_brightness(self.result[key], 40)
        for key in ("disabled", "reloadedOff"):
            self.assertFalse(self.result[key]["night"])
            self.assertEqual(self.result[key]["store"]["opos_night"], "false")
            self.assert_brightness(self.result[key], 85)

    def test_false_or_unknown_saved_switch_values_stay_off(self):
        for state in self.result["invalid"]:
            with self.subTest(value=state["store"]["opos_night"]):
                self.assertFalse(state["night"])
                self.assert_brightness(state, 85)

    def test_changing_saved_brightness_at_night_is_restored_at_dawn(self):
        self.assert_brightness(self.result["changedAtNight"], 40, "90")
        self.assert_brightness(self.result["changedAtDawn"], 90, "90")
        for key in ("changedAtNight", "changedAtDawn"):
            self.assertEqual(self.result[key]["store"]["opos_bright"], "90")

    def test_top_bar_volume_and_battery_keep_their_values_when_dimmed(self):
        for key, width, label in (("volume", "60%", "Simulated volume 60 percent, pull down for details"),
                                  ("battery", "61%", "61 percent, pull down for details")):
            with self.subTest(mode=key):
                state = self.result[key]
                self.assertEqual((state["width"], state["label"]), (width, label))
                self.assertEqual(state["filter"], "brightness(0.4)")
        self.assert_brightness(self.result["brightness"], 40)

    def test_non_demo_uses_displayed_phone_clock(self):
        state = self.result["realIgnoresSimulation"]
        self.assertEqual(state["time"], "12:00")
        self.assert_brightness(state, 85)

    def test_demo_clock_controls_both_status_bar_and_dimming(self):
        for key, time, effective in (("demoNight", "22:00", 40), ("demoQueryNight", "22:00", 40),
                                     ("demoWallTick", "22:00", 40),
                                     ("demoPreDawn", "06:59", 40), ("demoDawn", "07:00", 85)):
            with self.subTest(state=key):
                state = self.result[key]
                self.assertEqual(state["time"], "Demo time " + time)
                self.assert_brightness(state, effective)
                self.assertEqual(state["store"]["opos_bright"], "85")
                self.assertEqual(state["writes"], 0)

    def test_storage_denial_still_allows_switch_and_slider_to_work(self):
        initial = self.result["blockedDefault"]
        self.assertFalse(initial["night"])
        self.assertEqual(initial["filter"], "")
        self.assertEqual(initial["bright"], "100")
        for key in ("blockedEnabled", "blockedChanged"):
            self.assertTrue(self.result[key]["night"])
            self.assertEqual(self.result[key]["filter"], "brightness(0.4)")
        self.assertEqual(self.result["blockedDawn"]["filter"], "brightness(0.9)")
        self.assertEqual(self.result["blockedDawn"]["bright"], "90")
        for key in ("blockedDefault", "blockedEnabled", "blockedChanged", "blockedDawn"):
            self.assertEqual(self.result[key]["store"]["opos_bright"], "85")
            self.assertEqual(self.result[key]["writes"], 0)

    def test_full_brightness_restores_without_a_filter(self):
        self.assert_brightness(self.result["fullAtNight"], 40, "100")
        self.assert_brightness(self.result["fullAtDawn"], 100, "100")


if __name__ == "__main__":
    unittest.main()
