# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.governor import PowerState, Priority, Task, Mode, Governor
from opos.router import Router, afford, GOAL_BASE_DRAIN

class TestRouter(unittest.TestCase):
    def test_essential_always_allowed(self):
        state = PowerState(level=0, charging=False, health=0.5, temp_c=50, goal_hours=1)
        task = Task(name="e", priority=Priority.ESSENTIAL, cost=10)
        ok, reason = afford(state, task)
        self.assertTrue(ok)
        self.assertEqual(reason, "essential")

    def test_charging_allows_everything(self):
        state = PowerState(level=0, charging=True, health=0.1, temp_c=60)
        task = Task(name="u", priority=Priority.USEFUL, cost=5)
        ok, _ = afford(state, task)
        self.assertTrue(ok)

    def test_optional_floor(self):
        state_ok = PowerState(level=90, charging=False, health=1.0, temp_c=25)
        state_bad = PowerState(level=70, charging=False, health=1.0, temp_c=25)
        task = Task(name="o", priority=Priority.OPTIONAL, cost=1)
        ok, _ = afford(state_ok, task)
        self.assertTrue(ok)
        ok, _ = afford(state_bad, task)
        self.assertFalse(ok)

    def test_useful_cost(self):
        state_ok = PowerState(level=36, charging=False, health=1.0, temp_c=25)
        state_bad = PowerState(level=35, charging=False, health=1.0, temp_c=25)
        task = Task(name="u", priority=Priority.USEFUL, cost=2)
        ok, _ = afford(state_ok, task)
        self.assertTrue(ok)
        ok, _ = afford(state_bad, task)
        self.assertFalse(ok)

    def test_temp_defers_non_essential(self):
        state = PowerState(level=100, charging=False, health=1.0, temp_c=45)
        task_useful = Task(name="u", priority=Priority.USEFUL, cost=1)
        ok, _ = afford(state, task_useful)
        self.assertFalse(ok)
        task_ess = Task(name="e", priority=Priority.ESSENTIAL, cost=1)
        ok, _ = afford(state, task_ess)
        self.assertTrue(ok)

    def test_goal_case(self):
        state = PowerState(level=50, charging=False, health=1.0, temp_c=25, goal_hours=10)
        task = Task(name="u", priority=Priority.USEFUL, cost=1)
        eff = state.level * state.health
        goal = max(float(state.goal_hours), 0.5)
        expected = eff / goal - GOAL_BASE_DRAIN - task.cost >= 0
        ok, _ = afford(state, task)
        self.assertEqual(ok, expected)
        state2 = PowerState(level=10, charging=False, health=1.0, temp_c=25, goal_hours=0.1)
        task2 = Task(name="u", priority=Priority.USEFUL, cost=0)
        ok, _ = afford(state2, task2)
        eff2 = state2.level * state2.health
        expected2 = eff2 / 0.5 - GOAL_BASE_DRAIN - task2.cost >= 0
        self.assertEqual(ok, expected2)

    def test_mode_values(self):
        router = Router()
        state = PowerState(level=100, charging=False, health=1.0, temp_c=50)
        tasks = [Task("e", Priority.ESSENTIAL, 1), Task("u", Priority.USEFUL, 1)]
        dec = router(state, tasks)
        self.assertEqual(dec.mode, Mode.CRITICAL)
        state2 = PowerState(level=70, charging=False, health=1.0, temp_c=25)
        tasks2 = [Task("e", Priority.ESSENTIAL, 1), Task("o", Priority.OPTIONAL, 1), Task("u", Priority.USEFUL, 1)]
        dec2 = router(state2, tasks2)
        self.assertEqual(dec2.mode, Mode.SAVER)
        state3 = PowerState(level=90, charging=False, health=1.0, temp_c=25)
        tasks3 = [Task("o", Priority.OPTIONAL, 1)]
        dec3 = router(state3, tasks3)
        self.assertEqual(dec3.mode, Mode.NORMAL)

    def test_visible_reason(self):
        router = Router(visible=True)
        state = PowerState(level=90, charging=False, health=1.0, temp_c=25)
        tasks = [Task("o", Priority.OPTIONAL, 1), Task("u", Priority.USEFUL, 1)]
        dec = router(state, tasks)
        self.assertTrue(dec.reason.startswith("router: allowed"))
        self.assertIn("allowed 2", dec.reason)
        self.assertIn("deferred 0", dec.reason)

    def test_invalid_level_health(self):
        router = Router()
        tasks = [Task("e", Priority.ESSENTIAL, 1)]
        state_bad_level = PowerState(level=-1, charging=False, health=0.5, temp_c=25)
        with self.assertRaises(ValueError):
            router(state_bad_level, tasks)
        state_bad_health = PowerState(level=50, charging=False, health=1.5, temp_c=25)
        with self.assertRaises(ValueError):
            router(state_bad_health, tasks)

    def test_deterministic(self):
        router = Router()
        state = PowerState(level=50, charging=False, health=0.8, temp_c=30)
        tasks = [Task("a", Priority.USEFUL, 1), Task("b", Priority.OPTIONAL, 2)]
        d1 = router(state, tasks)
        d2 = router(state, tasks)
        self.assertEqual(d1, d2)

    def test_governor_delegates(self):
        router = Router()
        gov = Governor(advisor=router)
        state = PowerState(level=50, charging=False, health=1.0, temp_c=25)
        tasks = [Task("e", Priority.ESSENTIAL, 1), Task("u", Priority.USEFUL, 1)]
        dec = gov.decide(state, tasks)
        expected = router(state, tasks)
        self.assertEqual(dec, expected)

if __name__ == "__main__":
    unittest.main()
