# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos import power
from opos.governor import Task, Priority

class TestPower(unittest.TestCase):
    def setUp(self):
        self.tasks = (
            Task('calls', Priority.ESSENTIAL, 0.5),
            Task('messages', Priority.ESSENTIAL, 0.2),
            Task('email', Priority.USEFUL, 2),
            Task('maps', Priority.USEFUL, 4),
            Task('photos backup', Priority.OPTIONAL, 6),
        )

    def test_feature_drain(self):
        d = power.feature_drain()
        self.assertEqual(d, power.FEATURE_COST)
        d2 = power.feature_drain(steps=('dim screen',))
        self.assertAlmostEqual(d2['screen'], 3.6)
        d3 = power.feature_drain(health=0.5)
        for k in power.FEATURE_COST:
            self.assertAlmostEqual(d3[k], power.FEATURE_COST[k] * 2)

    def test_total_drain(self):
        total = power.total_drain(self.tasks)
        self.assertAlmostEqual(total, 23.5)
        total_paused = power.total_drain(self.tasks, paused=('photos backup',))
        self.assertAlmostEqual(total - total_paused, 6)

    def test_time_left_words(self):
        self.assertEqual(power.time_left_words(float('inf')), 'Lasts all day')
        self.assertEqual(power.time_left_words(0.5), 'About 30 min left')
        self.assertEqual(power.time_left_words(2.0), 'About 2 h left')
        self.assertEqual(power.time_left_words(2.5), 'About 2 h 30 min left')
        self.assertEqual(power.time_left_words(0.001), 'Almost empty')

    def test_plan_charging(self):
        p = power.plan(10, 100, self.tasks, charging=True)
        self.assertEqual(p.paused, ())
        self.assertTrue(p.meets_goal)
        self.assertIn('Charging', p.reason)

    def test_plan_no_goal(self):
        p = power.plan(50, None, self.tasks)
        self.assertEqual(p.steps, ())
        self.assertEqual(p.paused, ())

    def test_plan_easy(self):
        p = power.plan(80, 2, self.tasks)
        self.assertTrue(p.meets_goal)
        self.assertEqual(p.paused, ())

    def test_plan_moderate(self):
        p = power.plan(40, 6, self.tasks)
        self.assertTrue(p.meets_goal)
        names = ['dim screen', 'short screen timeout', 'batch sync', 'slow background polling']
        for i, s in enumerate(p.steps):
            self.assertEqual(s, names[i])
        paused = p.paused
        self.assertNotIn('calls', paused)
        self.assertNotIn('messages', paused)
        if paused:
            idx_pb = paused.index('photos backup') if 'photos backup' in paused else 999
            for name in ('email', 'maps'):
                if name in paused:
                    self.assertLess(idx_pb, paused.index(name))

    def test_plan_hard(self):
        p = power.plan(5, 24, self.tasks)
        self.assertFalse(p.meets_goal)
        self.assertIn('email', p.paused)
        self.assertIn('maps', p.paused)
        self.assertIn('photos backup', p.paused)
        self.assertIn('calls and messages kept on', p.reason)
        self.assertGreater(p.drain_per_h, 0)

    def test_plan_in_use(self):
        tasks2 = self.tasks + (Task('alarms', Priority.USEFUL, 0.1),)
        p = power.plan(20, 10, tasks2, in_use='maps')
        self.assertNotIn('alarms', p.paused)
        self.assertNotIn('maps', p.paused)
        if p.steps or p.paused:
            self.assertTrue(p.reason.startswith('Saving power for calls and messages and maps'))

    def test_static_low_power(self):
        p = power.static_low_power(40, self.tasks)
        self.assertEqual(p.steps, ('dim screen', 'batch sync'))
        self.assertEqual(p.paused, ('photos backup',))
        self.assertEqual(p.reason, 'Basic low-power mode')
        plan_p = power.plan(40, 24, self.tasks)
        self.assertGreater(power.important_hours(plan_p, 40), power.important_hours(p, 40))

    def test_minutes_gained(self):
        self.assertGreater(power.minutes_gained(40, 6, self.tasks), 0)
        self.assertEqual(power.minutes_gained(80, 2, self.tasks), 0)

if __name__ == '__main__':
    unittest.main()
