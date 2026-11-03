# SPDX-License-Identifier: GPL-3.0-or-later
"""Pointer ownership, cancellation and accessible names for the real top bar."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


STATIC = Path(__file__).resolve().parents[1] / "src" / "opos" / "web_static"

# Run the real script fragments in page order. This small DOM models event
# capture/bubbling and range sanitization; it is not a native-browser assertion.
HARNESS = r"""
const fs=require('fs'),vm=require('vm'),path=require('path');
const read=name=>fs.readFileSync(path.join(process.argv[1],name),'utf8');
const html=read('phone-b.html');
function run(mode='r',saved={},clock='12:00'){
  const ids={},store={opos_bar:mode,opos_bright:'100',opos_vol:'60',...saved},captures=new Map();
  let writes=0;
  function el(id){
    if(ids[id])return ids[id];
    let value='';
    const e=ids[id]={id,style:{},dataset:{},attrs:{},listeners:{},events:{},checked:false,
      textContent:'',offsetHeight:400,classList:{add(){},remove(){}},
      addEventListener(t,f,o){(this.listeners[t]||=[]).push({f,capture:o===true||!!(o&&o.capture)})},
      setAttribute(k,v){this.attrs[k]=String(v)},removeAttribute(k){delete this.attrs[k]},
      focus(){},setPointerCapture(p){captures.set(p,this)},
      dispatchEvent(ev){dispatch(this,ev);return true},
      get value(){return value},set value(v){
        if(this.type==='range'){
          const min=Number(this.min||0),max=Number(this.max||100),step=Number(this.step||1);
          let n=Number(v);if(v===''||!Number.isFinite(n))n=(min+max)/2;
          value=String(Math.max(min,Math.min(max,min+Math.round((n-min)/step)*step)));
        }else value=String(v);
      }};
    return e;
  }
  for(const id of ['bright','vol']){
    const tag=html.match(new RegExp('<input id="'+id+'"[^>]*>'))[0],e=el(id);
    for(const [,k,v] of tag.matchAll(/(\w+)="([^"]*)"/g))e[k]=v;
  }
  el('topbar').value='b';el('sb-time').textContent=clock;
  const win=el('window'),doc=el('document'),root=el('root');
  Object.assign(doc,{getElementById:el,documentElement:root,body:el('body'),querySelectorAll:()=>[]});
  function dispatch(target,ev){
    ev.target=target;ev.stopped=false;ev.immediate=false;
    ev.stopPropagation=()=>{ev.stopped=true};
    ev.stopImmediatePropagation=()=>{ev.stopped=ev.immediate=true};
    ev.preventDefault=()=>{};
    target.events[ev.type]=(target.events[ev.type]||0)+1;
    const route=target===win?[win]:target===doc?[win,doc]:[win,doc,target];
    function deliver(current,capture){
      ev.currentTarget=current;
      for(const listener of current.listeners[ev.type]||[]){
        if(listener.capture===capture)listener.f.call(current,ev);
        if(ev.immediate)break;
      }
    }
    for(const current of route){deliver(current,true);if(ev.stopped)return}
    for(const current of [...route].reverse()){
      deliver(current,false);if(ev.stopped||ev.bubbles===false)break;
    }
  }
  const c={document:doc,window:win,innerWidth:100,innerHeight:800,
    location:{search:''},URLSearchParams,requestAnimationFrame:f=>f(),
    Event:class{constructor(type){this.type=type;this.bubbles=false}},
    MutationObserver:class{observe(){}},
    localStorage:{getItem:k=>store[k]??null,setItem(k,v){store[k]=String(v);writes++}}};
  vm.createContext(c);
  for(const name of ['js-1.js','js-2.js','js-3.js'])vm.runInContext(read(name),c,{filename:name});
  vm.runInContext("barM={width:61,label:'61 percent'};barPaint()",c);
  function snapshot(){return {
    bright:el('bright').value,volume:el('vol').value,filter:root.style.filter,
    width:el('meter-fill').style.width,label:el('meter').attrs['aria-label'],
    expanded:el('meter').attrs['aria-expanded'],transform:el('sheet').style.transform||'',
    inputs:{bright:el('bright').events.input||0,vol:el('vol').events.input||0},
    changes:{bright:el('bright').events.change||0,vol:el('vol').events.change||0},
    state:vm.runInContext('({hitDown,sheetDrag,dragging,hitMoved,sheetOpen})',c),
    store:{...store},writes}}
  function fire(type,pointerId=0,x=95,y=5,id){
    const target=id?el(id):captures.get(pointerId)||win;
    dispatch(target,{type,pointerId,clientX:x,clientY:y,bubbles:true});
    if(type==='pointerup'||type==='pointercancel'||type==='lostpointercapture')captures.delete(pointerId);
    return snapshot();
  }
  return {snapshot,fire,down:(p=0,x=95,y=5)=>fire('pointerdown',p,x,y,'meter-hit'),
    selectMode(mode){el('topbar').value=mode;el('topbar').dispatchEvent(new c.Event('input'))},
    open:()=>win.opos.open(),range:{min:el('bright').min,step:el('bright').step}};
}
const out={};
for(const mode of ['r','v']){
  const m=out[mode]={};let r=run(mode);
  m.initial=r.snapshot();r.down(0,50,5);m.foreign=[];
  for(const type of ['pointermove','pointerup','pointercancel','lostpointercapture'])
    m.foreign.push(r.fire(type,2,20,70,'meter-hit'));
  m.ownerMove=r.fire('pointermove',0,50,30);m.foreignActive=[];
  for(const type of ['pointermove','pointerup','pointercancel','lostpointercapture'])
    m.foreignActive.push(r.fire(type,2,20,70,'meter-hit'));
  m.ownerEnd=r.fire('pointerup',0,50,70);
  m.secondaryDown=[];
  for(const moved of [false,true]){
    r=run(mode);r.down(0,50,5);if(moved)r.fire('pointermove',0,50,30);
    r.down(2,20,100);r.fire('pointermove',2,30,150);
    const afterForeign=r.snapshot(),afterOwner=r.fire('pointermove',0,50,70);
    m.secondaryDown.push({moved,afterForeign,afterOwner,afterCancel:r.fire('pointercancel',0,50,70)});
  }
  m.cancel={};m.sheetCancel={};
  for(const type of ['pointercancel','lostpointercapture']){
    r=run(mode);r.down(0,50,5);const changed=r.fire('pointermove',0,50,70);
    const cancelled=r.fire(type,0,50,70);
    const repeated=r.fire('lostpointercapture',0,50,70,'meter-hit');
    const stale=r.fire('pointermove',0,50,100);
    r.down(2,50,5);r.fire('pointermove',2,50,70);
    m.cancel[type]={changed,cancelled,repeated,stale,fresh:r.fire('pointerup',2,50,70)};
    r=run(mode);r.open();r.fire('pointerdown',0,50,100,'sheet-handle');
    const sheetMoved=r.fire('pointermove',0,50,10),sheetCancelled=r.fire(type,0,50,10);
    const sheetRepeated=r.fire('lostpointercapture',0,50,10,'sheet-handle');
    const sheetStale=r.fire('pointermove',0,50,0);
    r.fire('pointerdown',2,50,100,'sheet-handle');r.fire('pointermove',2,50,10);
    m.sheetCancel[type]={changed:sheetMoved,cancelled:sheetCancelled,repeated:sheetRepeated,
      stale:sheetStale,fresh:r.fire('pointerup',2,50,10)};
  }
  m.horizontal=[];
  for(const [from,to] of [[95,80],[5,80],[95,0],[50,100]]){
    r=run(mode);r.down(0,from,5);const moved=r.fire('pointermove',0,to,5);
    const ended=r.fire('pointerup',0,to,5),afterLoss=r.fire('lostpointercapture',0,to,5,'meter-hit');
    r.fire('click',0,to,5,'meter-hit');const suppressed=r.snapshot();
    r.fire('click',0,to,5,'meter-hit');
    m.horizontal.push({moved,ended,afterLoss,suppressed,fresh:r.snapshot()});
  }
  r=run(mode);r.down();r.selectMode(mode==='r'?'v':'r');
  m.modeSwitch={changed:r.fire('pointermove',0,80),cancelled:r.fire('pointercancel',0,80)};
}
let r=run('r');out.range=r.range;
out.storedMinimum=run('r',{opos_bright:'40'}).snapshot();
out.night=run('r',{opos_bright:'85',opos_night:'true'},'23:00').snapshot();
out.labels={brightness:run('r',{opos_bright:'80'}).snapshot(),volume:run('v').snapshot(),battery:run('b').snapshot()};
r=run('b');r.down(0,50,5);r.fire('pointermove',0,50,70);out.sheetOpened=r.fire('pointerup',0,50,70);
r.fire('pointerdown',0,50,100,'sheet-handle');
out.sheetForeignDown=r.down(2,50,100);
out.sheetForeignMove=r.fire('pointermove',2,50,10);
out.sheetOwnerMove=r.fire('pointermove',0,50,10);
out.sheetClosed=r.fire('pointerup',0,50,10);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node missing")
class TopBarFixesRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run(["node", "-e", HARNESS, str(STATIC)],
                              capture_output=True, text=True, timeout=20)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.result = json.loads(proc.stdout)

    def assert_idle(self, state):
        for key in ("hitDown", "sheetDrag", "dragging", "hitMoved"):
            self.assertFalse(state["state"][key], key)

    def assert_ranges_untouched(self, state):
        self.assertEqual((state["bright"], state["volume"]), ("100", "60"))
        self.assertEqual(state["store"]["opos_bright"], "100")
        self.assertEqual(state["store"]["opos_vol"], "60")
        self.assertEqual(state["writes"], 0)
        self.assertEqual(state["inputs"], {"bright": 0, "vol": 0})
        self.assertEqual(state["changes"], {"bright": 0, "vol": 0})

    def test_other_pointer_cannot_move_end_or_cancel_owner_gesture(self):
        for mode in ("r", "v"):
            with self.subTest(mode=mode):
                data = self.result[mode]
                for state in data["foreign"]:
                    self.assert_ranges_untouched(state)
                    self.assertEqual(state["transform"], "")
                    self.assertTrue(state["state"]["hitDown"])
                    self.assertFalse(state["state"]["sheetOpen"])
                self.assertEqual(data["ownerMove"]["transform"], "translateY(calc(-100% + 25px))")
                for state in data["foreignActive"]:
                    self.assertEqual(state, data["ownerMove"])
                self.assertTrue(data["ownerEnd"]["state"]["sheetOpen"])
                self.assert_ranges_untouched(data["ownerEnd"])
                self.assert_idle(data["ownerEnd"])

    def test_second_pointerdown_cannot_replace_owner_or_start_position(self):
        for mode in ("r", "v"):
            for data in self.result[mode]["secondaryDown"]:
                with self.subTest(mode=mode, moved=data["moved"]):
                    expected = "translateY(calc(-100% + 25px))" if data["moved"] else ""
                    self.assertEqual(data["afterForeign"]["transform"], expected)
                    self.assertTrue(data["afterForeign"]["state"]["hitDown"])
                    self.assertEqual(data["afterOwner"]["transform"], "translateY(calc(-100% + 65px))")
                    self.assertTrue(data["afterOwner"]["state"]["hitDown"])
                    for key in ("afterForeign", "afterOwner", "afterCancel"):
                        self.assert_ranges_untouched(data[key])
                    self.assertEqual(data["afterCancel"]["transform"], "")
                    self.assertFalse(data["afterCancel"]["state"]["sheetOpen"])
                    self.assert_idle(data["afterCancel"])

    def test_cancel_and_capture_loss_clear_vertical_pull_without_slider_events(self):
        for mode in ("r", "v"):
            for event, data in self.result[mode]["cancel"].items():
                with self.subTest(mode=mode, event=event):
                    self.assertEqual(data["changed"]["transform"], "translateY(calc(-100% + 65px))")
                    self.assertTrue(data["changed"]["state"]["dragging"])
                    state = data["cancelled"]
                    self.assertEqual(state["transform"], "")
                    self.assertFalse(state["state"]["sheetOpen"])
                    self.assertEqual(state["width"], self.result[mode]["initial"]["width"])
                    self.assertEqual(state["label"], self.result[mode]["initial"]["label"])
                    self.assert_ranges_untouched(state)
                    self.assert_idle(state)

    def test_cancel_cleanup_is_idempotent_and_stale_moves_do_nothing(self):
        for mode in ("r", "v"):
            for kind in ("cancel", "sheetCancel"):
                for event, data in self.result[mode][kind].items():
                    with self.subTest(mode=mode, kind=kind, event=event):
                        self.assertEqual(data["repeated"], data["cancelled"])
                        self.assertEqual(data["stale"], data["cancelled"])

    def test_fresh_gesture_works_after_cancel_or_capture_loss(self):
        for mode in ("r", "v"):
            for event, data in self.result[mode]["cancel"].items():
                with self.subTest(mode=mode, event=event):
                    self.assertTrue(data["fresh"]["state"]["sheetOpen"])
                    self.assert_ranges_untouched(data["fresh"])
                    self.assert_idle(data["fresh"])

    def test_sheet_drag_cancel_keeps_sheet_open_and_fresh_drag_can_close(self):
        for mode in ("r", "v"):
            for event, data in self.result[mode]["sheetCancel"].items():
                with self.subTest(mode=mode, event=event):
                    self.assertEqual(data["changed"]["transform"], "translateY(-90px)")
                    self.assertTrue(data["changed"]["state"]["sheetDrag"])
                    self.assertTrue(data["cancelled"]["state"]["sheetOpen"])
                    self.assertEqual(data["cancelled"]["transform"], "")
                    self.assertFalse(data["fresh"]["state"]["sheetOpen"])
                    for key in ("cancelled", "fresh"):
                        self.assert_ranges_untouched(data[key])
                        self.assert_idle(data[key])

    def test_horizontal_gesture_never_changes_ranges_or_storage(self):
        for mode in ("r", "v"):
            for index, data in enumerate(self.result[mode]["horizontal"]):
                with self.subTest(mode=mode, path=index):
                    for key in ("moved", "ended", "afterLoss"):
                        self.assert_ranges_untouched(data[key])
                        self.assertFalse(data[key]["state"]["sheetOpen"])
                    self.assertEqual(data["ended"], data["afterLoss"])
                    self.assert_idle(data["ended"])

    def test_drag_click_is_suppressed_but_fresh_click_opens_details(self):
        for mode in ("r", "v"):
            for index, data in enumerate(self.result[mode]["horizontal"]):
                with self.subTest(mode=mode, path=index):
                    self.assertFalse(data["suppressed"]["state"]["sheetOpen"])
                    self.assertTrue(data["fresh"]["state"]["sheetOpen"])

    def test_mode_change_before_horizontal_move_keeps_both_slider_values(self):
        for mode in ("r", "v"):
            with self.subTest(mode=mode):
                data = self.result[mode]["modeSwitch"]
                for key in ("changed", "cancelled"):
                    self.assert_ranges_untouched(data[key])
                self.assert_idle(data["cancelled"])

    def test_brightness_range_has_minimum_55_and_step_5(self):
        self.assertEqual(self.result["range"], {"min": "55", "step": "5"})
        state = self.result["storedMinimum"]
        self.assertEqual(state["bright"], "55")
        self.assertEqual(state["filter"], "brightness(0.55)")
        self.assertEqual(state["width"], "55%")

    def test_meter_names_include_mode_simulation_and_pull_down_action(self):
        expected = {"brightness": "Brightness 80 percent, pull down for details",
                    "volume": "Simulated volume 60 percent, pull down for details",
                    "battery": "61 percent, pull down for details"}
        for mode, label in expected.items():
            with self.subTest(mode=mode):
                self.assertEqual(self.result["labels"][mode]["label"], label)

    def test_night_dimming_stays_at_40_with_the_real_55_minimum(self):
        state = self.result["night"]
        self.assertEqual(state["bright"], "85")
        self.assertEqual(state["store"]["opos_bright"], "85")
        self.assertEqual(state["filter"], "brightness(0.4)")
        self.assertEqual(state["width"], "40%")
        self.assertEqual(state["label"], "Brightness 40 percent, pull down for details")
        self.assertEqual(state["writes"], 0)

    def test_vertical_sheet_drag_keeps_its_pointer_and_still_opens_and_closes(self):
        self.assertTrue(self.result["sheetOpened"]["state"]["sheetOpen"])
        self.assert_idle(self.result["sheetOpened"])
        self.assertTrue(self.result["sheetForeignDown"]["state"]["sheetDrag"])
        self.assertEqual(self.result["sheetForeignMove"]["transform"], "")
        self.assertEqual(self.result["sheetOwnerMove"]["transform"], "translateY(-90px)")
        self.assertFalse(self.result["sheetClosed"]["state"]["sheetOpen"])
        self.assert_idle(self.result["sheetClosed"])


if __name__ == "__main__":
    unittest.main()
