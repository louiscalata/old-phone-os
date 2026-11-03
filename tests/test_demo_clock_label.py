# SPDX-License-Identifier: GPL-3.0-or-later
"""Demo clocks share simulated time; native clocks keep local time."""
import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

from opos.web import Phone, page


STATIC = Path(__file__).resolve().parents[1] / 'src/opos/web_static'
NODE = shutil.which('node')


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids[attrs['id']] = attrs


HARNESS = r"""
const fs=require('fs'),vm=require('vm'),data=JSON.parse(fs.readFileSync(0,'utf8'));
const ids={},timers=[];let hour=8,minute=5;
for(const id of ['demo','phone','lock','unlock','lock-time','lock-date','lock-batt',
                'lock-ask','lock-call','lock-hangup','sb-time','sb-net','sb-engine','sb-batt']){
 const attrs=data.ids[id];
 ids[id]={textContent:'',hidden:'hidden' in attrs,inert:false,
  removeAttribute(k){delete attrs[k];if(k==='hidden')this.hidden=false},
  addEventListener(){},focus(){}};
}
const c={document:{getElementById:id=>ids[id]||null,addEventListener(){}},
 navigator:{onLine:true},location:{search:data.search},URLSearchParams,
 Date:class extends Date{constructor(){super(2026,10,3,hour,minute)}},
 setInterval(fn,ms){timers.push(fn)}};
c.window=c;vm.createContext(c);
// Include the existing query-string entry point before the phone-shell script.
vm.runInContext(fs.readFileSync(process.argv[1]+'/js-1.js','utf8'),c);
vm.runInContext(fs.readFileSync(process.argv[1]+'/phone-a.js','utf8'),c);
const read=()=>['sb-time','lock-time'].map(id=>ids[id].textContent);
const render=s=>c.oposHooks.forEach(fn=>fn(s));
const startup=read();
timers.forEach(fn=>fn());const initial=read();
render(data.states[0]);const first=read();
hour=21;minute=40;timers.forEach(fn=>fn());const paused=read();
render(data.states[1]);const advanced=read();
render({});timers.forEach(fn=>fn());const missing=read();
render(data.states[2]);c.oposUI.lock();const midnight=read();
console.log(JSON.stringify({startup,initial,first,paused,advanced,missing,midnight,
 date:ids['lock-date'].textContent,locked:!ids.lock.hidden&&ids.phone.inert,timers:timers.length}));
"""


class TestDemoClockLabel(unittest.TestCase):
    def states(self):
        phone = Phone()
        states = [phone.state()]
        phone.clock_s += 17 * 60
        states.append(phone.state())
        phone.clock_s = 86400
        states.append(phone.state())
        self.assertEqual([s['clock'] for s in states], ['12:00', '12:17', '00:00'])
        return states

    def run_clocks(self, demo=False, search=''):
        if not NODE:
            self.skipTest('node not installed')
        result = subprocess.run(
            [NODE, '-e', HARNESS, str(STATIC)],
            input=json.dumps({'ids': Elements(page(demo=demo)).ids,
                              'search': search, 'states': self.states()}),
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out['timers'], 2)
        self.assertTrue(out['locked'])
        self.assertTrue(out['date'])
        return out

    def assert_demo_clocks(self, out):
        for phase, time in [('startup', '—'), ('initial', '—'), ('first', '12:00'), ('paused', '12:00'),
                            ('advanced', '12:17'), ('missing', '12:17'), ('midnight', '00:00')]:
            self.assertEqual(out[phase], ['Demo time ' + time] * 2, phase)

    def test_demo_page_uses_one_labeled_simulated_clock(self):
        self.assert_demo_clocks(self.run_clocks(demo=True))

    def test_documented_demo_query_uses_simulated_clock(self):
        self.assert_demo_clocks(self.run_clocks(search='?demo=1'))

    def test_demo_query_with_other_options(self):
        self.assert_demo_clocks(self.run_clocks(search='?app=full&demo=1&lock=0'))

    def test_non_demo_keeps_local_clocks_without_label(self):
        for search in ['', '?demo=0', '?demo=10', '?app=full']:
            with self.subTest(search=search):
                out = self.run_clocks(search=search)
                self.assertEqual(out['startup'], ['08:05', ''])
                for phase in ['initial', 'first']:
                    self.assertEqual(out[phase], ['08:05'] * 2)
                for phase in ['paused', 'advanced', 'missing', 'midnight']:
                    self.assertEqual(out[phase], ['21:40'] * 2)


if __name__ == '__main__':
    unittest.main()
