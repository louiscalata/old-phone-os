# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos import power
from opos.power import SAVINGS_STEPS, steps_for_goal
from opos.runtime import DEFAULT_TASKS, Priority, Runtime, Task, Tick


class TestStepsForGoal(unittest.TestCase):
    def setUp(self):
        self.tasks = tuple(list(DEFAULT_TASKS) + [Task("alarms", Priority.USEFUL, 0.1)])

    def test_no_goal_returns_empty(self):
        self.assertEqual(steps_for_goal(80, None, self.tasks, ()), ())
        self.assertEqual(steps_for_goal(80, None, self.tasks, (), health=1.0), ())

    def test_tiny_goal_returns_empty(self):
        self.assertEqual(steps_for_goal(80, 0.1, self.tasks, (), health=1.0), ())

    def test_nonpositive_goal_returns_empty(self):
        self.assertEqual(steps_for_goal(80, 0, self.tasks, (), health=1.0), ())
        self.assertEqual(steps_for_goal(80, -5, self.tasks, (), health=1.0), ())

    def test_huge_goal_returns_all_four(self):
        result = steps_for_goal(80, 1000, self.tasks, (), health=1.0)
        self.assertEqual(result, tuple(s[0] for s in SAVINGS_STEPS))
        self.assertEqual(len(result), 4)

    def test_monotone_longer_goal_never_fewer_steps(self):
        prev = 0
        for goal in (1, 3, 5, 8, 11, 20, 50):
            cur = len(steps_for_goal(40, goal, self.tasks, (), health=0.6))
            self.assertGreaterEqual(cur, prev)
            prev = cur

    def test_prefix_property(self):
        names = [s[0] for s in SAVINGS_STEPS]
        for goal in (1, 4, 11, 100):
            result = steps_for_goal(40, goal, self.tasks, (), health=0.6)
            self.assertEqual(list(result), names[:len(result)])

    def test_level_zero_returns_all(self):
        self.assertEqual(steps_for_goal(0, 11, self.tasks, (), health=1.0), tuple(s[0] for s in SAVINGS_STEPS))


class TestRuntimeKeepsAlarms(unittest.TestCase):
    def test_keeps_alarms_allowed_at_low_level(self):
        tasks = tuple(list(DEFAULT_TASKS) + [Task("alarms", Priority.USEFUL, 0.1)])
        rt = Runtime(tasks=tasks)
        tick = rt.step(0.0, 3, health=1.0, goal_hours=11.0)
        self.assertIn("alarms", tick.decision.allowed)
        self.assertNotIn("alarms", tick.decision.deferred)

    def test_tick_steps_empty_without_goal(self):
        rt = Runtime()
        tick = rt.step(0.0, 40, health=0.6)
        self.assertEqual(tick.steps, ())

    def test_tick_steps_empty_when_charging(self):
        rt = Runtime()
        tick = rt.step(0.0, 40, charging=True, health=0.6, goal_hours=11.0)
        self.assertEqual(tick.steps, ())

    def test_tick_steps_nonempty_with_goal(self):
        tasks = tuple(list(DEFAULT_TASKS) + [Task("alarms", Priority.USEFUL, 0.1)])
        rt = Runtime(tasks=tasks)
        tick = rt.step(0.0, 40, health=0.6, goal_hours=11.0)
        self.assertTrue(len(tick.steps) > 0)
        names = [s[0] for s in SAVINGS_STEPS]
        self.assertEqual(list(tick.steps), names[:len(tick.steps)])

    def test_tick_default_steps(self):
        self.assertEqual(Tick.__dataclass_fields__["steps"].default, ())


if __name__ == "__main__":
    unittest.main()
