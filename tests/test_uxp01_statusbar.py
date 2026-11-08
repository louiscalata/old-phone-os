# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from opos.web import Phone, page


class TestStatusBar(unittest.TestCase):
    def test_statusbar_elements(self):
        html = page()
        for element in ('sb', 'sb-time', 'sb-net', 'sb-engine', 'sb-batt'):
            with self.subTest(element=element):
                self.assertIn('id="%s"' % element, html)
        self.assertIn('<div id="sb" role="status" aria-label="Status">', html)

    def test_statusbar_below_pulldown(self):
        html = page()
        sb = re.search(r'#sb\{([^}]+)\}', html).group(1)
        meter = re.search(r'#meter-hit\{([^}]+)\}', html).group(1)
        sb_z = int(re.search(r'z-index:\s*(\d+)', sb).group(1))
        meter_z = int(re.search(r'z-index:\s*(\d+)', meter).group(1))
        self.assertEqual(sb_z, 40)
        self.assertLess(sb_z, meter_z)
        self.assertIn('height:28px', sb)
        self.assertIn('position:fixed', sb)
        # UXP15 reserves the full 48px touch target above the page content.
        self.assertIn('body{padding-top:48px}', html)

    def test_statusbar_is_honest(self):
        js = (Path(__file__).resolve().parents[1] / 'src' / 'opos' /
              'web_static' / 'phone-a.js').read_text(encoding='utf-8')
        self.assertIn('navigator.onLine===false', js)
        self.assertNotIn('Online', js)
        self.assertIn('textContent', js)
        self.assertNotIn('innerHTML', js)
        self.assertIn('new Date()', js)
        self.assertIn('setInterval(tick,15000)', js)
        # Normal status bar time follows the visitor's local clock. The demo
        # notice clock is intentionally sourced from simulated state.
        self.assertIn("time.textContent=demoTime||ui.hhmm(new Date());", js)
        self.assertIn("ui.hhmm=function(d){return ui.clockOk()?('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2):'--:--'};", js)
        hook = js[js.index('window.oposHooks.push(function(s){try{', js.index('var time=G(\'sb-time\')')):]
        self.assertIn('var {clock}=s;if(demoTime&&clock)demoTime=\'Demo time \'+clock;', hook)
        self.assertIn('tick();', hook)
        self.assertNotIn('time.textContent=s.clock', hook)
        self.assertIn("isFinite(s.battery.percent)", js)
        self.assertIn("Math.round(s.battery.percent)+'%'", js)
        self.assertIn("engine.textContent=s.helper?'Helper on':'';", js)
        self.assertNotIn('Helper set to', js)  # the bar says the helper is on, not which model (it is configured, not verified)

    def test_lock_clock_updates_without_state_render(self):
        html = page()
        lock = html.split("var cover=document.getElementById('lock')", 1)[1].split('</script>', 1)[0]
        clock = lock.split('function lockTick(){try{', 1)[1].split('}catch(e)', 1)[0]
        self.assertIn('new Date()', clock)
        for element in ('lock-time', 'lock-date'):
            self.assertIn("document.getElementById('%s').textContent=" % element, clock)
        self.assertIn('setInterval(lockTick,15000)', lock)
        self.assertIn('window.oposHooks.push(function(s){try{\n      lockTick();', lock)
        self.assertIn('}catch(e){window.oposUI.unlock();}', lock)

    def test_lock_accessibility_and_independent_exit(self):
        html = page()
        self.assertRegex(html, r'<div id="lock"[^>]*aria-modal="true"')
        lock = html.split("var cover=document.getElementById('lock')", 1)[1].split('</script>', 1)[0]
        locking = lock.split('window.oposUI.lock=function(){try{', 1)[1].split('}catch(e)', 1)[0]
        self.assertIn("document.getElementById('unlock').focus()", locking)
        self.assertIn("if(e.key==='Escape')window.oposUI.unlock()", lock)
        self.assertIn("if(!/(^|[?&])(lock=0|demo=1|app=full)(&|$)/.test(location.search))window.oposUI.lock();", lock)
        unlock = lock.split('// unlock definition start', 1)[1].split('// unlock definition end', 1)[0]
        self.assertIn('cover.hidden=true', unlock)
        self.assertIn('phone.inert=false', unlock)
        self.assertNotIn('fetch', unlock)
        self.assertNotIn('api(', unlock)

    @unittest.skipUnless(shutil.which('node'), 'node missing')
    def test_unknown_clock_is_drawn_as_unknown(self):
        """The server's "--:--" (or a year before 2026) is shown as unknown: status bar, and the Home dateline's own refresh."""
        static = Path(__file__).resolve().parents[1] / 'src/opos/web_static'
        harness = r"""
const fs=require('fs'),vm=require('vm'),root=process.argv[1];
let year=2026,calls=0;
const ids={};for(const id of ['sb-time','sb-net','sb-engine','sb-batt'])ids[id]={textContent:''};
const window={oposHooks:[],oposUI:{dateline(){calls++}}};
class D extends Date{constructor(){super(year,10,3,8,5)}}
vm.runInNewContext(fs.readFileSync(root+'/phone-a.js','utf8'),{document:{getElementById:id=>ids[id]||null},window,navigator:{onLine:true},Date:D,setInterval(){}});
const hooks=s=>window.oposHooks.forEach(f=>f(s)),read=()=>ids['sb-time'].textContent,out={};
out.boot=read();
hooks({clock:'--:--',battery:{percent:30}});out.unset=[read(),window.oposUI.clockOk()];
hooks({clock:'08:05',battery:{percent:30}});out.set=[read(),window.oposUI.clockOk()];
year=1970;hooks({clock:'08:05',battery:{percent:30}});out.epoch=[read(),window.oposUI.clockOk()];
year=2026;hooks({battery:{percent:30}});out.noClock=read();
out.dateline=calls>0;
console.log(JSON.stringify(out));
"""
        proc = subprocess.run(['node', '-e', harness, str(static)], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out['boot'], '08:05')
        self.assertEqual(out['unset'], ['--:--', False])
        self.assertEqual(out['set'], ['08:05', True])
        self.assertEqual(out['epoch'], ['--:--', False])
        self.assertEqual(out['noClock'], '08:05')
        self.assertTrue(out['dateline'], 'tick() refreshes the Home dateline')

    @unittest.skipUnless(shutil.which('node'), 'node missing')
    def test_home_dateline_says_time_not_set_and_recovers(self):
        static = Path(__file__).resolve().parents[1] / 'src/opos/web_static'
        harness = r"""
const fs=require('fs'),vm=require('vm'),root=process.argv[1];
class El{constructor(t){this.tag=t;this.kids=[];this.classList={contains(){return false},add(){}}}
 appendChild(c){this.kids.push(c);return c}insertBefore(c){this.kids.unshift(c);return c}
 get firstChild(){return this.kids[0]||null}querySelectorAll(){return[]}}
const phone=new El('div');let ok=true,writes=0;
const ctx={document:{readyState:'complete',getElementById:id=>id==='phone'?phone:null,createElement:t=>{
  const e=new El(t);let text='';Object.defineProperty(e,'textContent',{get:()=>text,set:v=>{text=String(v);writes++}});return e},
  addEventListener(){},documentElement:{dataset:{}}},oposUI:{date:()=>ok?'Tuesday, March 3':'Time not set'},
 oposHooks:[],matchMedia:()=>({matches:false}),localStorage:{getItem:()=>null,setItem(){},removeItem(){}}};
ctx.window=ctx;vm.createContext(ctx);vm.runInContext(fs.readFileSync(root+'/jaryo.js','utf8'),ctx);
const mast=phone.kids[0],date=mast.kids[2],out={known:date.textContent==='Tuesday, March 3'};
ok=false;ctx.oposUI.dateline();out.unset=date.textContent;
const w=writes;ctx.oposUI.dateline();out.quiet=writes===w;
ok=true;ctx.oposUI.dateline();out.back=date.textContent==='Tuesday, March 3';
console.log(JSON.stringify(out));
"""
        proc = subprocess.run(['node', '-e', harness, str(static)], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {'known': True, 'unset': 'Time not set', 'quiet': True, 'back': True})

    def test_quiet_mode_has_no_engine(self):
        self.assertNotIn('helper', Phone(visible=False).state())

    BAR = r"""
const fs=require('fs'),vm=require('vm');
const ids={};
for(const id of ['sb-time','sb-net','sb-engine','sb-batt'])ids[id]={textContent:'',attrs:{},setAttribute(k,v){this.attrs[k]=v}};
const window={oposHooks:[]};let online=true;
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{
 document:{getElementById:id=>ids[id]||null},window,navigator:{get onLine(){return online}},Date,setInterval(){}});
const hooks=s=>window.oposHooks.forEach(f=>f(s));
const bar=s=>{hooks(s);return{net:ids['sb-net'].textContent,batt:ids['sb-batt'].textContent,low:ids['sb-batt'].attrs['data-low'],engine:ids['sb-engine'].textContent}};
const out={};
"""

    def run_bar(self, script):
        source = Path(__file__).resolve().parents[1] / 'src/opos/web_static/phone-a.js'
        proc = subprocess.run(['node', '-e', self.BAR + script + "\nconsole.log(JSON.stringify(out));", str(source)],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    @unittest.skipUnless(shutil.which('node'), 'node missing')
    def test_configured_label_does_not_claim_a_reply_and_clears_in_quiet_mode(self):
        out = self.run_bar("""
out.configured=bar({helper:{engine:'Muse Glimmer, on this device'},battery:{percent:30}}).engine;
out.quiet=bar({battery:{percent:30}}).engine;""")
        self.assertEqual(out, {'configured': 'Helper on', 'quiet': ''})

    @unittest.skipUnless(shutil.which('node'), 'node missing')
    def test_network_slot_says_in_call_then_the_note_then_offline(self):
        out = self.run_bar("""
const b={percent:50};
out.call=bar({in_call:true,status_note:'Simulated battery',battery:b}).net;
out.note=bar({status_note:'Simulated battery',battery:b}).net;
out.clear=bar({status_note:'',battery:b}).net;
online=false;
out.offline=bar({status_note:'',battery:b}).net;
out.noteBeatsOffline=bar({status_note:'Clock not set',battery:b}).net;
out.callBeatsOffline=bar({in_call:true,battery:b}).net;""")
        self.assertEqual(out, {'call': 'In call', 'note': 'Simulated battery', 'clear': '',
                               'offline': 'Offline', 'noteBeatsOffline': 'Clock not set', 'callBeatsOffline': 'In call'})

    @unittest.skipUnless(shutil.which('node'), 'node missing')
    def test_low_battery_has_a_word_and_the_double_border_together(self):
        out = self.run_bar("""
for(const p of [100,16,15,12,4,0])out[p]=bar({battery:{percent:p}});
out.unknown=bar({battery:{}});out.missing=bar({});""")
        for pct, low in ((100, False), (16, False)):
            self.assertEqual((out[str(pct)]['batt'], out[str(pct)]['low']), (f'{pct}%', 'false'))
        for pct in (15, 12, 4, 0):
            self.assertEqual((out[str(pct)]['batt'], out[str(pct)]['low']), (f'LOW {pct}%', 'true'))
        self.assertEqual((out['unknown']['batt'], out['unknown']['low']), ('—', 'false'))
        self.assertEqual((out['missing']['batt'], out['missing']['low']), ('—', 'false'))

    def test_simulated_telephony_is_disclosed_where_controls_appear(self):
        self.assertIn('<div id="controls"><p id="demo-phone-note">Calls and texts may use your configured phone connection.</p>', page())
        css = (Path(__file__).resolve().parents[1] / 'src/opos/web_static/phone-b.css').read_text()
        for app, actions in (('phone', ('send', 'receive')), ('messages', ('call', 'hang_up'))):
            for action in actions:
                self.assertIn(f'#phone[data-app="{app}"] #controls [data-go="{action}"]', css)

    def test_notice_can_wrap_at_large_text_sizes(self):
        css = (Path(__file__).resolve().parents[1] / 'src/opos/web_static/css-4.css').read_text()
        notice = re.search(r'#notice\{([^}]+)\}', css).group(1)
        self.assertIn('min-height:20px', notice)
        self.assertIn('height:auto', notice)
        self.assertNotIn('overflow:hidden', notice)


if __name__ == '__main__':
    unittest.main()
