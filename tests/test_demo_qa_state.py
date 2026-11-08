# SPDX-License-Identifier: GPL-3.0-or-later
"""Public QA regressions for real state, SVG rendering and scoped markup."""
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

from opos import public_demo, web
from opos.dashboard import Dashboard


STATIC = Path(__file__).parents[1] / 'src/opos/web_static'
NODE = shutil.which('node')


class Elements(HTMLParser):
    """Retain element ancestry without treating void tags as containers."""
    def __init__(self, source):
        super().__init__()
        self.stack, self.ids = [], {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        entry = {'tag': tag, 'attrs': attrs, 'ancestors': list(self.stack), 'text': ''}
        if attrs.get('id'):
            self.ids[attrs['id']] = entry
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(entry)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]['text'] += data


def node_json(test, script, payload):
    if not NODE:
        test.skipTest('node unavailable')
    result = subprocess.run([NODE, '-e', script], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=10)
    test.assertEqual(result.returncode, 0, result.stderr)
    return json.loads(result.stdout)


class DemoQAState(unittest.TestCase):
    def test_slider_sequence_preserves_real_recent_levels(self):
        phone = public_demo.DemoPhone()
        expected = [60]
        self.assertEqual(phone.state()['battery']['history'], expected)
        for level in (20, 19, 60, 30):
            phone.set_battery(level)
            expected.append(level)
            state = phone.state()['battery']
            self.assertEqual(state['history'], expected)
            self.assertEqual(state['history'][-1], phone.sim.level)
            self.assertEqual(state['percent'], level)

    def test_repeated_same_slider_value_does_not_add_samples(self):
        phone = public_demo.DemoPhone()
        for level in (60, 60, 20, 20, 20):
            phone.set_battery(level)
            phone.state()  # Polling must not invent additional samples either.
        self.assertEqual(phone.state()['battery']['history'], [60, 20])

    def test_normal_ticks_record_drain_and_keep_at_most_sixty_samples(self):
        phone = public_demo.DemoPhone()
        phone.sim.speed = 60
        phone._last_tick_time = 0
        with mock.patch('opos.web.time.monotonic') as clock:
            for second in range(1, 63):
                clock.return_value = second
                phone.tick(1)
                state = phone.state()['battery']
                self.assertLessEqual(len(state['history']), 60)
                self.assertEqual(state['history'][-1], round(phone.sim.level, 1))
                if second == 1:
                    self.assertEqual(len(state['history']), 2)
                    self.assertLess(state['history'][-1], state['history'][0])
        self.assertEqual(len(state['history']), 60)
        self.assertNotIn(60, state['history'])
        self.assertTrue(all(t >= phone.clock_s - 3600 for t in phone.dash._times))

    def test_changed_samples_expire_after_an_hour_even_within_one_minute(self):
        dash = Dashboard((), history_len=500)
        dash.record(60, 0)
        dash.record(20, 1, changed=True)
        dash.record(30, 3601, changed=True)
        self.assertEqual(dash.history, [20, 30])
        self.assertEqual(dash._times, [1, 3601])
        phone = public_demo.DemoPhone()
        phone.set_battery(20)
        phone.clock_s += 3601
        self.assertEqual(phone.state()['battery']['history'], [])

    def test_many_slider_changes_are_bounded_and_clock_reset_drops_old_day(self):
        phone = public_demo.DemoPhone()
        for index in range(100):
            phone.set_battery(20 if index % 2 else 60)
        self.assertEqual(len(phone.state()['battery']['history']), 60)
        phone.clock_s = 0
        phone.set_battery(30)
        state = phone.state()['battery']
        self.assertNotIn(60, state['history'])
        self.assertEqual(state['history'][-1], 30)
        self.assertEqual(len(phone.dash.history), len(phone.dash._times))
        self.assertEqual(phone.dash._times[-1], 0)

    def test_real_svg_renderer_changes_points_with_phone_history(self):
        phone = public_demo.DemoPhone()
        histories = [phone.state()['battery']['history']]
        for level in (20, 19, 60, 30):
            phone.set_battery(level)
            histories.append(phone.state()['battery']['history'])
        source = (STATIC / 'js-7.js').read_text()
        fragment = source[source.index('const poly='):source.index("sync(G('features')")]
        rendered = node_json(self, r"""
const vm = require('node:vm'), fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = input.histories.map(history => {
  let points;
  const document = {querySelector(selector) {
    if (selector !== '#history polyline') throw new Error(selector);
    return {setAttribute(name, value) {
      if (name !== 'points') throw new Error(name);
      points = value;
    }};
  }};
  vm.runInNewContext(input.fragment, {document, s: {battery: {history}}});
  return points;
});
process.stdout.write(JSON.stringify(out));
""", {'histories': histories, 'fragment': fragment})
        self.assertEqual(histories[-1][-1], 30)
        self.assertEqual(len(set(rendered)), len(histories))
        self.assertEqual(len(histories[0]), 1)
        self.assertEqual(rendered[0], '')  # W1-07: one sample draws no line (it used to draw a flat "last hour")
        for points in rendered[1:]:
            vertices = [tuple(map(float, point.split(','))) for point in points.split()]
            self.assertEqual(vertices[0][0], 0)
            self.assertEqual(vertices[-1][0], 100)
            self.assertTrue(all(math.isfinite(v) for point in vertices for v in point))
            self.assertTrue(all(0 <= x <= 100 and 4 <= y <= 56 for x, y in vertices))
        final = [tuple(map(float, point.split(','))) for point in rendered[-1].split()]
        self.assertEqual(len(final), 5)
        self.assertEqual(final[0][1], final[3][1])  # Both real 60% samples.
        self.assertGreater(final[2][1], final[1][1])  # 19% is below 20%.
        self.assertLess(final[-1][1], final[1][1])  # 30% recovered above 20%.

    def test_technical_reason_is_inside_collapsed_details(self):
        for page in (web.page(), public_demo.landing('', 'n')):
            with self.subTest(public='public-demo' in page):
                elements = Elements(page)
                why = elements.ids['why']
                details = elements.ids['shade-technical']
                self.assertIn(details, why['ancestors'])
                self.assertEqual(details['tag'], 'details')
                self.assertNotIn('open', details['attrs'])

    def test_temperature_label_tracks_input_and_polling_without_fighting_drag(self):
        page = web.page()
        output = Elements(page).ids['temp-val']
        self.assertEqual(output['tag'], 'output')
        self.assertEqual(output['attrs']['for'], 'temp')
        self.assertEqual(output['text'], '30°C')
        source = (STATIC / 'js-8.js').read_text()
        oninput = next(line for line in source.splitlines() if "G('temp').oninput=" in line)
        start = page.index('if(s.temp_c!=null')
        end = page.index("G('charge').checked", start)
        poll = page[start:end]
        result = node_json(self, r"""
const vm = require('node:vm'), fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const els = {temp: {value: '30'}, 'temp-val': {textContent: '30°C'}};
const document = {activeElement: null};
const context = vm.createContext({G: id => els[id], document, s: {temp_c: 44.5}});
vm.runInContext(input.oninput, context);
els.temp.value = '42.5';
els.temp.oninput({target: els.temp});
const dragged = els['temp-val'].textContent;
document.activeElement = els.temp;
vm.runInContext(input.poll, context);
const focused = [els.temp.value, els['temp-val'].textContent];
document.activeElement = null;
vm.runInContext(input.poll, context);
process.stdout.write(JSON.stringify({dragged, focused, polled: [els.temp.value, els['temp-val'].textContent]}));
""", {'oninput': oninput, 'poll': poll})
        self.assertEqual(result['dragged'], '42.5°C')
        self.assertEqual(result['focused'], ['42.5', '42.5°C'])
        self.assertEqual(result['polled'], [44.5, '44.5°C'])


if __name__ == '__main__':
    unittest.main()
