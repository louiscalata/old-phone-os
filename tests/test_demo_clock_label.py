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
const ids={},timers=[];let hour=8,minute=5;const year=data.year||2026;
for(const id of ['demo','phone','lock','unlock','lock-time','lock-date','lock-batt',
                'lock-ask','lock-call','lock-hangup','sb-time','sb-net','sb-engine','sb-batt']){
 const attrs=data.ids[id];
 ids[id]={textContent:'',hidden:'hidden' in attrs,inert:false,
  removeAttribute(k){delete attrs[k];if(k==='hidden')this.hidden=false},
  addEventListener(){},focus(){}};
}
const c={document:{getElementById:id=>ids[id]||null,addEventListener(){}},
 navigator:{onLine:true},location:{search:data.search},URLSearchParams,
 Date:class extends Date{constructor(){super(year,10,3,hour,minute)}},
 setInterval(fn,ms){timers.push(fn)}};
c.window=c;vm.createContext(c);
// Include the existing query-string entry point before the phone-shell script.
vm.runInContext(fs.readFileSync(process.argv[1]+'/js-1.js','utf8'),c);
vm.runInContext(fs.readFileSync(process.argv[1]+'/phone-a.js','utf8'),c);
const read=()=>['sb-time','lock-time'].map(id=>ids[id].textContent);
const render=s=>c.oposHooks.forEach(fn=>fn(s));
const startup=read();
timers.forEach(fn=>fn());const initial=read();
render(data.states[0]);const first=read();const dateFirst=ids['lock-date'].textContent;
hour=21;minute=40;timers.forEach(fn=>fn());const paused=read();
render(data.states[1]);const advanced=read();
render({});timers.forEach(fn=>fn());const missing=read();
render(data.states[2]);c.oposUI.lock();const midnight=read();
console.log(JSON.stringify({startup,initial,first,paused,advanced,missing,midnight,dateFirst,
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

    def run_clocks(self, demo=False, search='', states=None, year=2026):
        if not NODE:
            self.skipTest('node not installed')
        result = subprocess.run(
            [NODE, '-e', HARNESS, str(STATIC)],
            input=json.dumps({'ids': Elements(page(demo=demo)).ids, 'year': year,
                              'search': search, 'states': states or self.states()}),
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

    def test_unknown_clock_is_unknown_on_the_status_bar_and_the_lock(self):
        # The server's "--:--" (clock not set, or only raised to the image's floor) wins over the browser's own clock.
        known = self.states()
        unset = [dict(s, clock='--:--') for s in known]
        out = self.run_clocks(states=unset)
        self.assertEqual(out['first'], ['--:--', '--:--'])
        self.assertEqual(out['dateFirst'], 'Time not set')
        self.assertEqual(out['paused'], ['--:--', '--:--'], 'the timers do not bring a guessed time back')
        # Once the server has a real clock the same page agrees with it on the next render.
        out = self.run_clocks(states=unset[:1] + known[1:])
        self.assertEqual(out['advanced'], ['21:40'] * 2)
        self.assertNotEqual(out['date'], 'Time not set')

    def test_a_browser_clock_before_2026_is_unknown_even_before_the_first_render(self):
        # The Duo can boot at 1970: nothing is drawn as a time from the first paint.
        out = self.run_clocks(year=1970)
        self.assertEqual(out['startup'], ['--:--', ''])
        self.assertEqual(out['initial'], ['--:--', '--:--'])
        self.assertEqual(out['first'], ['--:--', '--:--'])
        self.assertEqual(out['dateFirst'], 'Time not set')
        self.assertEqual(out['date'], 'Time not set')

    def test_the_demo_clock_is_not_overridden_by_the_unknown_rule(self):
        out = self.run_clocks(demo=True, year=1970)
        self.assert_demo_clocks(out)


if __name__ == '__main__':
    unittest.main()
