# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from opos.power import GOAL_ARRIVE_LEVEL, SAVINGS_STEPS, keep_goal_steps, steps_for_goal
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
            self.assertEqual(list(result), names[: len(result)])

    def test_level_zero_returns_all(self):
        self.assertEqual(steps_for_goal(0, 11, self.tasks, (), health=1.0), tuple(s[0] for s in SAVINGS_STEPS))

    def test_arrive_level_or_below_returns_all(self):
        names = tuple(s[0] for s in SAVINGS_STEPS)
        self.assertEqual(GOAL_ARRIVE_LEVEL, 0.5)
        self.assertEqual(steps_for_goal(GOAL_ARRIVE_LEVEL, 11, self.tasks, (), health=1.0), names)
        self.assertEqual(steps_for_goal(0.4, 11, self.tasks, (), health=1.0), names)


class TestKeepGoalSteps(unittest.TestCase):
    def test_keeps_the_longer_prefix(self):
        names = tuple(s[0] for s in SAVINGS_STEPS)
        self.assertEqual(keep_goal_steps((), ()), ())
        self.assertEqual(keep_goal_steps(names[:3], names[:1]), names[:3])
        self.assertEqual(keep_goal_steps(names[:1], names[:3]), names[:3])
        self.assertEqual(keep_goal_steps(names[:2], names), names)
        self.assertEqual(keep_goal_steps((), names[:2]), names[:2])


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
        self.assertEqual(list(tick.steps), names[: len(tick.steps)])

    def test_tick_default_steps(self):
        self.assertEqual(Tick.__dataclass_fields__["steps"].default, ())

    def test_does_not_drop_steps_as_the_goal_nears(self):
        rt = Runtime()
        early = rt.step(0.0, 60.0, health=0.6, temp_c=30.0, goal_hours=4.0, goal="16:00")
        self.assertEqual(early.steps, ("dim screen", "short screen timeout", "batch sync"))
        # A short remaining horizon would choose no steps if recomputed from scratch.
        late = rt.step(3.75 * 3600, 4.84, health=0.6, temp_c=30.0, goal_hours=0.25, goal="16:00")
        self.assertEqual(late.steps, early.steps)
        self.assertLessEqual(late.drain_per_h, early.drain_per_h)

    def test_resets_steps_when_charging(self):
        rt = Runtime()
        held = rt.step(0.0, 60.0, health=0.6, temp_c=30.0, goal_hours=4.0, goal="16:00")
        self.assertTrue(held.steps)
        charging = rt.step(60.0, 60.0, charging=True, health=0.6, temp_c=30.0, goal_hours=4.0, goal="16:00")
        self.assertEqual(charging.steps, ())
        # After unplugging, steps are planned again rather than restored blindly.
        again = rt.step(120.0, 80.0, health=1.0, temp_c=30.0, goal_hours=0.1, goal="16:00")
        self.assertEqual(again.steps, ())

    def test_resets_steps_when_goal_is_reached(self):
        rt = Runtime()
        rt.step(0.0, 60.0, health=0.6, temp_c=30.0, goal_hours=4.0, goal="16:00")
        reached = rt.step(4.0 * 3600, 8.0, health=0.6, temp_c=30.0, goal_hours=None, goal="16:00")
        self.assertEqual(reached.steps, ())

    def test_resets_steps_when_the_goal_changes(self):
        rt = Runtime()
        first = rt.step(0.0, 60.0, health=0.6, temp_c=30.0, goal_hours=4.0, goal="16:00")
        self.assertTrue(first.steps)
        # A later, easily reached goal should not keep the previous goal's steps.
        changed = rt.step(60.0, 80.0, health=1.0, temp_c=30.0, goal_hours=0.1, goal="18:00")
        self.assertEqual(changed.steps, ())


if __name__ == "__main__":
    unittest.main()
