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
        self.assertIn("t=demoTime||('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);\nif(time.textContent!==t)time.textContent=t;", js)
        hook = js[js.index('window.oposHooks.push(function(s){try{', js.index('var time=G(\'sb-time\')')):]
        self.assertIn('var {clock}=s;if(demoTime&&clock)demoTime=\'Demo time \'+clock;', hook)
        self.assertIn('tick();', hook)
        self.assertNotIn('time.textContent=s.clock', hook)
        self.assertIn("isFinite(s.battery.percent)", js)
        self.assertIn("Math.round(s.battery.percent)+'%'", js)
        self.assertIn("typeof s.helper.engine==='string'", js)

    def test_lock_clock_updates_without_state_render(self):
        html = page()
        lock = html.split("var cover=G('lock')", 1)[1].split('</script>', 1)[0]
        clock = lock.split('function lockTick(){try{', 1)[1].split('}catch(e)', 1)[0]
        self.assertIn('new Date()', clock)
        for element in ('lock-time', 'lock-date'):
            self.assertIn("G('%s')" % element, clock)
        # W1-09: the 15 s timer writes a text only when it differs (a write replaces the text node and dirties the layout)
        self.assertIn('if(a.textContent!==t)a.textContent=t;\nif(b.textContent!==n)b.textContent=n;', clock)
        self.assertIn('setInterval(lockTick,15000)', lock)
        self.assertIn('window.oposHooks.push(function(s){try{\n      lockTick();', lock)
        self.assertIn('}catch(e){window.oposUI.unlock();}', lock)

    def test_lock_accessibility_and_independent_exit(self):
        html = page()
        self.assertRegex(html, r'<div id="lock"[^>]*aria-modal="true"')
        lock = html.split("var cover=G('lock')", 1)[1].split('</script>', 1)[0]
        locking = lock.split('window.oposUI.lock=function(){try{', 1)[1].split('}catch(e)', 1)[0]
        self.assertIn("G('unlock').focus()", locking)
        self.assertIn("if(e.key==='Escape')window.oposUI.unlock()", lock)
        self.assertIn("if(!/(^|[?&])(lock=0|demo=1|app=full)(&|$)/.test(location.search)||/(^|[?&])practice=/.test(location.search))window.oposUI.lock();", lock)
        unlock = lock.split('// unlock definition start', 1)[1].split('// unlock definition end', 1)[0]
        self.assertIn('cover.hidden=true', unlock)
        self.assertIn('phone.inert=false', unlock)
        self.assertNotIn('fetch', unlock)
        self.assertNotIn('api(', unlock)

    def test_quiet_mode_has_no_engine(self):
        self.assertNotIn('helper', Phone(visible=False).state())

    @unittest.skipUnless(shutil.which('node'), 'node missing')
    def test_configured_label_does_not_claim_a_reply_and_clears_in_quiet_mode(self):
        source = Path(__file__).resolve().parents[1] / 'src/opos/web_static/phone-a.js'
        harness = r"""
const fs=require('fs'),vm=require('vm'),ids={};
for(const id of ['sb-time','sb-net','sb-engine','sb-batt'])ids[id]={textContent:''};
const window={oposHooks:[]};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{
 document:{getElementById:id=>ids[id]||null},window,navigator:{onLine:true},Date,setInterval(){}});
const hooks=s=>window.oposHooks.forEach(f=>f(s));
hooks({helper:{engine:'Muse Glimmer, on this device'},battery:{percent:30}});
const configured=ids['sb-engine'].textContent;hooks({battery:{percent:30}});
console.log(JSON.stringify({configured,quiet:ids['sb-engine'].textContent}));
"""
        proc = subprocess.run(['node', '-e', harness, str(source)], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {
            'configured': 'Helper on', 'quiet': ''})

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
