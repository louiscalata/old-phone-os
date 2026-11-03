# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock stories describe effective pauses and announce only changed wording."""
import json
import re
import subprocess
import unittest
from unittest import mock

from opos.advisor import GlimmerAdvisor
from opos.web import Phone, lock_story, page
from tests.test_uxp11_lock_shade import HARNESS as LOCK_HARNESS, JS, NODE


HARNESS = LOCK_HARNESS.split("const out={},render=", 1)[0]
HARNESS = HARNESS.replace("'lock-notice','unlock'", "'lock-notice','lock-story','unlock'")
HARNESS = HARNESS.replace(
    "c.document.addEventListener=function(){};",
    "const keys={};c.document.addEventListener=function(name,fn){keys[name]=fn};")
HARNESS += r"""
let minute=0;
c.Date=class extends Date{getHours(){return 12}getMinutes(){return minute}};
const out={},render=s=>c.oposHooks.forEach(f=>f(s)),story=get('lock-story');
const text='Worn battery. 2 tasks paused for 4 PM.';
render({battery:{percent:60},lock_story:text});
out.first=[story.textContent,story.writes,!get('lock').hidden,get('phone').inert];
out.timeBefore=get('lock-time').textContent;
minute=1;
render({battery:{percent:59},lock_story:text});
out.same=[story.writes,get('lock-batt').textContent,get('lock-time').textContent];
render({battery:{percent:59},lock_story:'Charging. Nothing paused.'});
out.changed=[story.textContent,story.writes];
render({lock_story:'<b>Maps paused</b>'});
out.plain=[story.textContent,story.children.length];
get('unlock').onclick();
out.button=[get('lock').hidden,get('phone').inert];
c.oposUI.lock();
keys.keydown({key:'Escape'});
out.escape=[get('lock').hidden,get('phone').inert];
render({});
const writes=story.writes;
render({});
out.missing=[story.textContent,story.writes===writes];
console.log(JSON.stringify(out));
"""


class TestLockStory(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch('opos.web.prefs.load', side_effect=OSError))
        self.enterContext(mock.patch('opos.web.assist.free_mb', return_value=4096))

    def test_worn_story_with_helper_on_or_off(self):
        stories = []
        for visible in (False, True):
            with self.subTest(visible=visible):
                phone = Phone(visible=visible)
                state = phone.state()
                stories.append(state['lock_story'])
                self.assertEqual('helper' in state, visible)
                self.assertEqual({t['name'] for t in state['battery']['paused']},
                                 {'maps', 'photos backup'})
                self.assertEqual(state['lock_story'], 'Worn battery. Maps and photos backup paused.')
                self.assertNotIn('60%', state['lock_story'])
        self.assertEqual(stories[0], stories[1])

    def test_charging_and_healthy_phone(self):
        phone = Phone()
        phone.dash.set_goal('16:00')
        phone.charging = True
        state = phone.state()
        self.assertEqual(state['battery']['paused'], [])
        self.assertEqual(state['lock_story'], 'Charging. Nothing paused.')
        phone.charging = False
        phone.dash.set_goal(None)
        phone.sim.level = 90
        phone.sim.health = 1
        self.assertEqual(phone.state()['lock_story'], 'Nothing paused.')

    def test_charging_does_not_hide_an_effective_pause(self):
        self.assertEqual(lock_story(60, .6, 30, True, ['maps'], '16:00', False),
                         'Charging. Maps paused.')

    def test_low_and_hot_are_distinct(self):
        phone = Phone(visible=True)
        phone.sim.level = 8
        phone.sim.health = 1
        state = phone.state()
        self.assertEqual(state['lock_story'], 'Battery low. 3 tasks paused.')
        self.assertIn('calls', state['helper']['allowed'])
        self.assertIn('messages', state['helper']['allowed'])
        self.assertNotIn('only', state['lock_story'])
        phone.sim.level = 90
        phone.sim.temp_c = 46
        state = phone.state()
        self.assertTrue(state['lock_story'].startswith('Phone hot. '))
        self.assertNotIn('Battery low', state['lock_story'])

    def test_reachable_and_unreachable_goal_use_actual_estimate(self):
        phone = Phone()
        phone.dash.set_goal('16:00')
        state = phone.state()
        self.assertTrue(state['battery']['goal_met'])
        self.assertEqual(state['lock_story'], 'Worn battery. 2 tasks paused for 4 PM.')
        phone.sim.level = 10
        phone.dash.set_goal('23:59')
        state = phone.state()
        self.assertFalse(state['battery']['goal_met'])
        self.assertEqual(state['lock_story'], 'Battery low. 3 tasks paused; 11:59 PM unlikely.')
        self.assertNotIn('last until', state['lock_story'])

    def test_undo_and_in_use_remove_pauses_from_story(self):
        phone = Phone()
        phone.state()
        phone.dash.undo('maps')
        state = phone.state()
        self.assertEqual(state['lock_story'], 'Worn battery. Photos backup paused.')
        phone.dash.set_in_use('photos backup')
        state = phone.state()
        self.assertEqual(state['battery']['paused'], [])
        self.assertEqual(state['lock_story'], 'Worn battery. Nothing paused.')

    def test_extra_helper_pause_is_included_without_model_request(self):
        phone = Phone(visible=True)
        jobs = []
        phone.runtime.governor.advisor = GlimmerAdvisor(
            phone.runtime.governor.advisor, route='fixture',
            ask=lambda *args, **kwargs: '{"defer":["email"]}',
            spawn=jobs.append, clock=lambda: 0)
        before = phone.state()
        self.assertEqual(len(jobs), 1)
        jobs[0]()
        state = phone.state()
        self.assertNotIn('email', {t['name'] for t in before['battery']['paused']})
        self.assertIn('email', {t['name'] for t in state['battery']['paused']})
        self.assertIn('email', state['helper']['paused'])
        self.assertEqual(state['lock_story'], 'Worn battery. 3 tasks paused.')

    def test_long_names_and_reordered_tasks_keep_a_short_stable_story(self):
        args = (60, .6, 30, False)
        first = lock_story(*args, ['maps', 'email'], '16:00')
        self.assertEqual(first, lock_story(*args, ['email', 'maps', 'email'], '16:00'))
        for names in ([], ['maps'], ['maps', 'email'], ['photos backup', 'maps', 'email'], ['x' * 100]):
            for goal_met in (False, True):
                with self.subTest(names=names, goal_met=goal_met):
                    story = lock_story(*args, names, '16:00', goal_met)
                    self.assertLessEqual(len(story), 49)
                    self.assertNotIn('\n', story)
                    self.assertNotRegex(story, r'\b(?:AI|model|Governor|assistant)\b')
        self.assertEqual(lock_story(*args, ['x' * 100]), 'Worn battery. 1 task paused.')

    def test_live_region_is_separate_from_unlock_and_percent(self):
        html = page()
        tags = re.findall(r'<div\b[^>]*id="lock-story"[^>]*>', html)
        self.assertEqual(len(tags), 1)
        for attr in ('role="status"', 'aria-live="polite"', 'aria-atomic="true"'):
            self.assertIn(attr, tags[0])
        self.assertLess(html.index('id="lock-batt"'), html.index('id="lock-story"'))
        self.assertLess(html.index('id="lock-story"'), html.index('id="unlock"'))
        css = re.search(r'#lock-story\{([^}]+)\}', html).group(1)
        self.assertIn('overflow-wrap:anywhere', css)
        self.assertNotIn('position:absolute', css)
        self.assertNotIn('white-space:nowrap', css)

    @unittest.skipUnless(NODE, 'node not installed')
    def test_refresh_updates_only_changed_story_and_keeps_unlock(self):
        result = subprocess.run([NODE, '--unhandled-rejections=strict', '-e', HARNESS, str(JS)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out['first'], ['Worn battery. 2 tasks paused for 4 PM.', 1, True, True])
        self.assertEqual(out['timeBefore'], '12:00')
        self.assertEqual(out['same'], [1, '59%', '12:01'])
        self.assertEqual(out['changed'], ['Charging. Nothing paused.', 2])
        self.assertEqual(out['plain'], ['<b>Maps paused</b>', 0])
        self.assertEqual(out['button'], [True, False])
        self.assertEqual(out['escape'], [True, False])
        self.assertEqual(out['missing'], ['', True])


if __name__ == '__main__':
    unittest.main()
