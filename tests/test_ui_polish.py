# SPDX-License-Identifier: GPL-3.0-or-later
"""Judge-visible UI regressions, using the real static scripts in Node's VM."""
import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest


STATIC = Path(__file__).resolve().parents[1] / 'src' / 'opos' / 'web_static'


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.stack, self.ids = [], {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids[attrs['id']] = (tag, attrs, self.stack.copy())
        if tag not in {'input', 'br', 'meta', 'link', 'img', 'hr'}:
            self.stack.append((tag, attrs.get('id')))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class HelperClockMarkup(unittest.TestCase):
    def test_plan_credit_is_inside_ask_next_to_plan(self):
        html = (STATIC / 'body-4.html').read_text()
        tags = Markup(html).ids
        self.assertIn(('div', 'ask'), tags['glimmer-credit'][2])
        self.assertLess(html.index('id="planbtn"'), html.index('id="glimmer-credit"'))
        self.assertLess(html.index('id="glimmer-credit"'), html.index('id="answer"'))
        self.assertNotIn('style', tags['glimmer-credit'][1])

    def test_status_clock_is_quiet_and_battery_status_is_retained(self):
        tags = Markup((STATIC / 'phone-a.html').read_text()).ids
        self.assertEqual(tags['sb-time'][1].get('aria-live'), 'off')
        self.assertEqual(tags['sb'][1]['role'], 'status')
        self.assertIn(('div', 'sb'), tags['sb-batt'][2])
        self.assertNotEqual(tags['sb-batt'][1].get('aria-live'), 'off')


def node(script):
    proc = subprocess.run(['node', '-e', script, str(STATIC)],
                          capture_output=True, text=True, timeout=20)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


HELPER_HARNESS = r"""
const fs=require('fs'),vm=require('vm'),path=require('path');
const ids={},G=id=>ids[id]||(ids[id]={textContent:'',addEventListener(){}}),window={};
let reply,release;
const c={window,document:{getElementById:G},G,meterEl:G('meter'),hitEl:G('hit'),vis:true,
 helperCall:async()=>reply};
vm.runInNewContext(fs.readFileSync(path.join(process.argv[1],'js-2.js'),'utf8'),c);
const state=visible=>{c.vis=visible;for(const f of window.oposHooks)f({helper:visible?{}:null,battery:{why:''}})};
const snapshot=()=>({answer:G('answer').textContent,credit:G('glimmer-credit').textContent});
const plan={order:['dim screen'],engine:'Muse Glimmer',credit:'Muse Glimmer kept this order',where:'on this device'};
(async()=>{
 state(true);reply=plan;await G('planbtn').onclick();const shown=snapshot();
 state(false);const hidden=snapshot();state(true);const reshown=snapshot();
 reply=new Promise(r=>release=r);const pending=G('planbtn').onclick();
 state(false);release(plan);await pending;const late=snapshot();state(true);
 console.log(JSON.stringify({shown,hidden,reshown,late,lateReshown:snapshot()}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""


@unittest.skipUnless(shutil.which('node'), 'node missing')
class HelperResetRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = node(HELPER_HARNESS)

    def test_hiding_clears_answer_and_credit_even_without_reason_change(self):
        self.assertTrue(self.result['shown']['answer'].startswith('Plan:'))
        self.assertEqual(self.result['shown']['credit'], 'Helper: no extra pauses; kept the plan order')
        self.assertEqual(self.result['hidden'], {'answer': '', 'credit': 'Helper: off'})
        self.assertEqual(self.result['reshown'], {'answer': '', 'credit': 'Helper: no extra pauses'})

    def test_late_plan_reply_cannot_restore_answer_after_hide(self):
        self.assertEqual(self.result['late'], {'answer': '', 'credit': 'Helper: off'})
        self.assertEqual(self.result['lateReshown'], {'answer': '', 'credit': 'Helper: no extra pauses'})

    def test_late_ask_reply_cannot_restore_answer_after_hide(self):
        harness = HELPER_HARNESS.replace('helperCall:async()=>reply',
            'helperAsk:async()=>reply,helperCall:async()=>reply')
        harness = harness.replace("const pending=G('planbtn').onclick()", "const pending=G('askbtn').onclick()")
        harness = harness.replace('release(plan)', "release({text:'Stale answer',engine:'Muse Glimmer',where:'on this device'})")
        result = node(harness)
        self.assertEqual(result['late'], {'answer': '', 'credit': 'Helper: off'})
        self.assertEqual(result['lateReshown'], {'answer': '', 'credit': 'Helper: no extra pauses'})

    def test_hiding_cancels_active_helper_before_clearing_answer(self):
        harness = HELPER_HARNESS.replace('let reply,release;', 'let reply,release,cancelled=0;')
        harness = harness.replace('state(false);const hidden=',
            "G('helpercancel').onclick=()=>{cancelled++;G('answer').textContent='Cancelled';G('helpercancel').onclick=null};state(false);const hidden=")
        harness = harness.replace('{shown,hidden,reshown,late,', '{cancelled,shown,hidden,reshown,late,')
        result = node(harness)
        self.assertEqual(result['cancelled'], 1)
        self.assertEqual(result['hidden'], {'answer': '', 'credit': 'Helper: off'})


PAUSED_HARNESS = r"""
const fs=require('fs'),vm=require('vm'),path=require('path');
const source=fs.readFileSync(path.join(process.argv[1],'js-7.js'),'utf8');
const rows=source.slice(0,source.indexOf('const poly='));
const document={activeElement:null};let clears=0,apiCalls=[];
const pl={children:[],dataset:{},querySelectorAll(){return this.children.map(r=>r.details).filter(d=>d.open)},
 appendChild(row){this.children.push(row)},set innerHTML(v){if(v!=='')throw Error('Unexpected list markup');
  clears++;this.children=[];document.activeElement=null}};
function E(){const row={button:{},details:{dataset:{},open:false}};
 Object.defineProperty(row,'innerHTML',{set(markup){this.markup=markup;this.details.dataset.n=markup.match(/data-n="([^"]*)"/)[1];this.details.open=/<details[^>]* open>/.test(markup)}});
 row.querySelector=s=>s==='button'?row.button:row.details;return row}
const c={G:()=>pl,E,document,esc:s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'),
 api:o=>{apiCalls.push(o);return Promise.resolve({})},render(){}};
vm.createContext(c);vm.runInContext('function renderRows(s){'+rows+'}',c);
const p={id:'maps',name:'Maps',reason:'Battery rules paused this.'};
const render=paused=>c.renderRows({battery:{paused}});
const same=()=>pl.children[0]===first&&document.activeElement===first.button&&first.details.open;
render([p]);const first=pl.children[0];first.details.open=true;document.activeElement=first.button;
render([{reason:p.reason,name:p.name,id:p.id}]);const stable=same();
render([{...p,extra:'not rendered'}]);const unrelated=same();
const stableClears=clears;first.button.onclick();
render([{...p,reason:'Muse Glimmer paused this.'}]);const changed={rebuilt:pl.children[0]!==first,open:pl.children[0].details.open,markup:pl.children[0].markup};
const changedRow=pl.children[0];render([{...p,name:'Navigation'}]);const renamed=pl.children[0]!==changedRow;
render([]);const empty=pl.children.length;const beforeEmpty=clears;render([]);render(undefined);
console.log(JSON.stringify({stable,unrelated,stableClears,apiCalls,changed,renamed,empty,emptyStable:clears===beforeEmpty}));
"""


@unittest.skipUnless(shutil.which('node'), 'node missing')
class PausedRowsRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = node(PAUSED_HARNESS)

    def test_unchanged_pauses_keep_nodes_focus_and_open_details(self):
        self.assertTrue(self.result['stable'])
        self.assertTrue(self.result['unrelated'])
        self.assertEqual(self.result['stableClears'], 1)
        self.assertEqual(self.result['apiCalls'], [{'action': 'undo', 'name': 'maps'}])

    def test_changed_reason_and_name_refresh_rows_and_keep_open_disclosure(self):
        self.assertTrue(self.result['changed']['rebuilt'])
        self.assertTrue(self.result['changed']['open'])
        self.assertIn('Muse Glimmer paused this.', self.result['changed']['markup'])
        self.assertTrue(self.result['renamed'])

    def test_removal_clears_rows_once_and_empty_ticks_do_not_rebuild(self):
        self.assertEqual(self.result['empty'], 0)
        self.assertTrue(self.result['emptyStable'])


class SliderMarkup(unittest.TestCase):
    # The page size cap is enforced once, in tests/test_uxp30_budget.py.
    def test_sliders_have_visible_associated_outputs_and_adjacent_night_note(self):
        tags = Markup((STATIC / 'phone-b.html').read_text()).ids
        for ident in ('bright', 'vol'):
            with self.subTest(slider=ident):
                output = tags.get(ident + '-val')
                self.assertIsNotNone(output)
                self.assertEqual(output[0], 'output')
                self.assertEqual(output[1].get('for'), ident)
                self.assertNotIn('hidden', output[1])
                self.assertEqual(output[2], tags[ident][2])
        self.assertIn('night-note', tags)
        self.assertEqual(tags['night-note'][2], tags['bright'][2])

    def test_meter_caption_is_rendered_visibly_from_its_caption(self):
        css = (STATIC / 'css-1.css').read_text()
        self.assertRegex(css, r'#meter::after\{[^}]*content:attr\(data-caption\)')


BAR_HARNESS = r"""
const fs=require('fs'),vm=require('vm'),path=require('path');
const read=name=>fs.readFileSync(path.join(process.argv[1],name),'utf8');
function run(o={}){
 const ids={},store={opos_bright:'85',opos_vol:'60',opos_bar:'r',...o.store};let writes=0;
 const el=id=>ids[id]||(ids[id]={id,value:'',textContent:'',style:{},dataset:{},attrs:{},listeners:{},
  addEventListener(t,f){(this.listeners[t]||=[]).push(f)},
  dispatchEvent(e){for(const f of this.listeners[e.type]||[])f(e)},
  setAttribute(k,v){this.attrs[k]=String(v)},removeAttribute(k){delete this.attrs[k]},focus(){}});
 for(const id of ['bright','vol']){
  const tag=read('phone-b.html').match(new RegExp('<input id="'+id+'"[^>]*>'))[0];
  for(const [,k,v] of tag.matchAll(/(\w+)="([^"]*)"/g))el(id)[k]=v;
 }
 el('sb-time').textContent=o.clock||'12:00';el('demo').hidden=!o.demo;
 const window=el('window'),root=el('root');
 const c={G:el,window,barM:undefined,document:{getElementById:el,documentElement:root,addEventListener(){},activeElement:null},
  meterEl:el('meter'),handleEl:el('handle'),titleEl:el('title'),timeEl:el('time'),gripEl:el('grip'),scrimEl:el('scrim'),
  closeSheet(){},MutationObserver:class{observe(){}},
  localStorage:{getItem(k){if(o.blocked)throw Error('denied');return store[k]??null},
   setItem(k,v){if(o.blocked)throw Error('denied');store[k]=String(v);writes++}}};
 vm.createContext(c);vm.runInContext(read('js-3.js'),c);
 vm.runInContext('function renderState(s){'+read('js-6.js')+'}}',c);
 const snap=()=>({bright:el('bright').value,volume:el('vol').value,
  brightOutput:el('bright-val').textContent,volumeOutput:el('vol-val').textContent,
  note:el('night-note').textContent,caption:el('meter').dataset.caption,
  tone:el('meter').dataset.tone,width:el('meter-fill').style.width,filter:root.style.filter,
  label:el('meter').attrs['aria-label'],store:{...store},writes});
 const paint=()=>{vm.runInContext('barPaint()',c);return snap()};
 return {snap,paint,
  input(id,value){const e=el(id);if(id==='night')e.checked=value;else e.value=String(value);e.dispatchEvent({type:'input'});return snap()},
  save(id){el(id).dispatchEvent({type:'change'});return snap()},
  clock(t){el('sb-time').textContent=t;return paint()},
  state(clock,tone='low'){c.renderState({clock,battery:{percent:11,meter:{width:11,label:'11 percent',tone}}});return snap()}};
}
const out={},r=run();out.initial=r.snap();out.bright=r.input('bright',80);out.volume=r.input('vol',25);
out.brightSaved=r.save('bright');out.volumeSaved=r.save('vol');out.reload=run({store:out.volumeSaved.store}).snap();
out.tones=[];for(const tone of ['low','charging','full']){
 for(const mode of ['r','v','b']){r.input('topbar',mode);out.tones.push({mode,tone,state:r.state('12:00',tone)})}
}
const n=run({store:{opos_night:'true'},clock:'21:59'});out.night=[n.snap()];
for(const time of ['22:00','23:59','00:00','06:59','07:00'])out.night.push(n.clock(time));
n.clock('23:00');out.savedNight=n.input('bright',90);out.savedNight=n.save('bright');out.dawn=n.clock('07:00');
n.clock('23:00');out.nightDisabled=n.input('night',false);
const demo=run({demo:true,store:{opos_night:'true'}});out.demoNight=demo.state('22:00');out.demoDawn=demo.state('07:00');
const denied=run({blocked:true,clock:'23:00'});denied.input('night',true);out.denied=denied.input('bright',80);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which('node'), 'node missing')
class SliderNightRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = node(BAR_HARNESS)

    def test_visible_slider_outputs_initialize_update_on_input_and_reload(self):
        self.assertEqual(self.result['initial']['brightOutput'], '85%')
        self.assertEqual(self.result['initial']['volumeOutput'], '60%')
        self.assertEqual(self.result['bright']['brightOutput'], '80%')
        self.assertEqual(self.result['volume']['volumeOutput'], '25%')
        self.assertEqual(self.result['volume']['writes'], 0)
        self.assertEqual(self.result['reload']['brightOutput'], '80%')
        self.assertEqual(self.result['reload']['volumeOutput'], '25%')

    def test_top_meter_captions_and_tone_follow_mode_on_every_state_render(self):
        for row in self.result['tones']:
            with self.subTest(mode=row['mode'], battery_tone=row['tone']):
                state = row['state']
                self.assertEqual(state['caption'], {'r': 'Brightness', 'v': 'Volume', 'b': ''}[row['mode']])
                self.assertEqual(state['tone'], row['tone'] if row['mode'] == 'b' else '')
                self.assertEqual(state['width'], {'r': '80%', 'v': '25%', 'b': '11%'}[row['mode']])

    def test_night_note_tracks_both_boundaries_without_mutating_saved_brightness(self):
        for state, night in zip(self.result['night'], [False, True, True, True, True, False]):
            with self.subTest(state=state):
                self.assertEqual(state['note'], '40% until 07:00' if night else '')
                self.assertEqual(state['width'], '40%' if night else '85%')
                self.assertEqual(state['brightOutput'], '85%')
                self.assertEqual(state['bright'], '85')
                self.assertEqual(state['store']['opos_bright'], '85')
                self.assertEqual(state['writes'], 0)
        self.assertEqual(self.result['nightDisabled']['note'], '')

    def test_night_note_uses_demo_clock_and_survives_storage_denial(self):
        for key in ('demoNight', 'denied'):
            self.assertEqual(self.result[key]['note'], '40% until 07:00')
            self.assertEqual(self.result[key]['filter'], 'brightness(0.4)')
        self.assertEqual(self.result['demoDawn']['note'], '')
        self.assertEqual(self.result['denied']['brightOutput'], '80%')

    def test_night_level_and_note_use_one_constant(self):
        # Change only the shared constant in the VM; both uses must follow it.
        import re
        source = (STATIC / 'js-3.js').read_text()
        match = re.search(r'\b(\w+)=40\b', source)
        self.assertIsNotNone(match, 'night level must be named once')
        harness = BAR_HARNESS.replace("vm.runInContext(read('js-3.js'),c)",
            "vm.runInContext(read('js-3.js').replace(" + json.dumps(match[0]) + "," +
            json.dumps(match[1] + '=35') + "),c)")
        changed = node(harness)['night'][1]
        self.assertEqual(changed['note'], '35% until 07:00')
        self.assertEqual(changed['width'], '35%')
        self.assertEqual(changed['filter'], 'brightness(0.35)')

    def test_brightness_change_at_night_updates_saved_output_and_restores_at_dawn(self):
        night, dawn = self.result['savedNight'], self.result['dawn']
        self.assertEqual(night['brightOutput'], '90%')
        self.assertEqual(night['note'], '40% until 07:00')
        self.assertEqual(night['store']['opos_bright'], '90')
        self.assertEqual(night['filter'], 'brightness(0.4)')
        self.assertEqual(dawn['note'], '')
        self.assertEqual(dawn['filter'], 'brightness(0.9)')
        self.assertEqual(dawn['store']['opos_bright'], '90')


# Reuse the acceptance driver's sandbox-compatible Chromium transport for the
# visible caption and real helper cancellation, including its progress timer.
NATIVE_CHECK = r"""
CHECKS.polish=async function(){
 const out=[];
 await load('home',390,844,false);
 await ev('window.__polish='+JSON.stringify(STATE.visible)+';render(__polish)');
 await ev("oposUI.openApp('settings');G('bright').value='80';G('bright').dispatchEvent(new Event('input'));G('vol').value='25';G('vol').dispatchEvent(new Event('input'));G('topbar').value='r';G('topbar').dispatchEvent(new Event('input'))");
 if(!await ev("__u.vis(G('bright-val'))&&__u.vis(G('vol-val'))&&G('bright-val').textContent==='80%'&&G('vol-val').textContent==='25%'"))out.push('slider values not visible');
 for(const [mode,caption] of [['r','Brightness'],['v','Volume']]){
  await ev("G('topbar').value="+JSON.stringify(mode)+";__polish.battery.meter.tone='charging';render(__polish)");
  if(!await ev("G('meter').dataset.tone===''&&getComputedStyle(G('meter')).animationName==='none'&&getComputedStyle(G('meter'),'::after').content==="+JSON.stringify('"'+caption+'"')))out.push('meter caption or battery tone '+mode);
 }
 await ev("G('topbar').value='r';G('night').checked=true;__polish.clock='22:00';render(__polish)");
 if(!await ev("__u.vis(G('night-note'))&&G('night-note').textContent==='40% until 07:00'&&G('bright').value==='80'&&G('bright-val').textContent==='80%'"))out.push('night floor not explained');
 await ev("__polish.clock='07:00';render(__polish)");
 if(!await ev("G('night-note').textContent===''&&document.documentElement.style.filter==='brightness(0.8)'"))out.push('night note not cleared at dawn');
 await ev("oposUI.openApp('helper');window.__originalFetch=fetch;window.__aborted=false;window.fetch=(u,o)=>{if(JSON.parse(o&&o.body||'{}').action==='plan')return new Promise((resolve,reject)=>o.signal.addEventListener('abort',()=>{__aborted=true;reject(new DOMException('Aborted','AbortError'))}));return __originalFetch(u,o)};G('planbtn').click()");
 if(!await waitFor("document.getElementById('helperwait')"))out.push('helper never started');
 await ev('render('+JSON.stringify(STATE.hidden)+')');
 await ev('render('+JSON.stringify(STATE.visible)+')');
 if(!await ev("__aborted&&helperTimer===null&&!G('helperwait')&&!G('helpercancel')&&G('answer').textContent===''&&G('glimmer-credit').textContent==='Helper: off'"))out.push('hidden helper request or progress survived');
 return out;
};
"""


from tests import test_uxr_acceptance as acceptance


class PolishBrowser(unittest.TestCase):
    skip_reason = None
    mode = 'port'
    setUpClass = classmethod(acceptance.TestUxrAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.TestUxrAcceptance.tearDownClass.__func__)
    setUp = acceptance.TestUxrAcceptance.setUp
    tearDown = acceptance.TestUxrAcceptance.tearDown

    def test_native_visible_values_captions_night_note_and_helper_cancellation(self):
        import os
        driver = Path(self.run_dir) / 'polish.js'
        driver.write_text(acceptance.DRIVER.read_text().replace(
            '(async () => {', NATIVE_CHECK + '\n(async () => {', 1))
        target = str(self.port) if self.proc is not None else 'pipe'
        env = dict(os.environ, UXR_CHROME=self.chrome, UXR_PIDFILE=self.pidfile, TMPDIR=self.run_dir)
        result = subprocess.run([self.node, str(driver), target, self.url, 'polish', self.state],
                                capture_output=True, text=True, timeout=40, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])['violations'], [])
