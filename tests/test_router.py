# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.governor import PowerState, Priority, Task, Mode, Governor
from opos.power import SAVINGS_STEPS, total_drain
from opos.router import Router, afford, GOAL_BASE_DRAIN
from opos.runtime import DEFAULT_TASKS, Runtime

ALL_STEPS = tuple(s[0] for s in SAVINGS_STEPS)

class TestRouter(unittest.TestCase):
    def test_essential_always_allowed(self):
        state = PowerState(level=0, charging=False, health=0.5, temp_c=50, goal_hours=1)
        task = Task(name="e", priority=Priority.ESSENTIAL, cost=10)
        ok, reason = afford(state, task)
        self.assertTrue(ok)
        self.assertEqual(reason, "essential")

    def test_charging_allows_everything_below_the_heat_line(self):
        state = PowerState(level=0, charging=True, health=0.1, temp_c=44.9)
        task = Task(name="u", priority=Priority.USEFUL, cost=5)
        self.assertEqual(afford(state, task), (True, "charging"))

    def test_heat_applies_while_charging(self):
        for temp_c in (45, 60):
            with self.subTest(temp_c=temp_c):
                state = PowerState(level=60, charging=True, health=1.0, temp_c=temp_c)
                self.assertEqual(afford(state, Task("maps", Priority.USEFUL, 4)), (False, "too hot"))
                self.assertEqual(afford(state, Task("calls", Priority.ESSENTIAL, 0.5)), (True, "essential"))
                dec = Router()(state, DEFAULT_TASKS)
                self.assertEqual(dec.allowed, ("calls", "messages"))
                self.assertEqual(dec.mode, Mode.CRITICAL)

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

    def test_goal_budget_is_shared_by_the_allowed_tasks(self):
        # 42% at full health for 4 h leaves 4.33 %/h: email (2) fits, then maps (4) no longer does.
        state = PowerState(level=42, charging=False, health=1.0, temp_c=25, goal_hours=4)
        dec = Router()(state, DEFAULT_TASKS)
        self.assertEqual(dec.allowed, ("calls", "messages", "email"))
        self.assertEqual(dec.deferred, ("maps", "photos backup"))
        # On its own maps would fit; together with email it does not.
        self.assertEqual(afford(state, Task("maps", Priority.USEFUL, 4)), (True, "fits your goal"))
        hours = state.level / total_drain(DEFAULT_TASKS, paused=dec.deferred, steps=ALL_STEPS, health=state.health)
        self.assertGreaterEqual(hours, 4)

    def test_allowed_tasks_fit_the_goal_whenever_essentials_alone_fit(self):
        checked = 0
        for health in (0.35, 0.6, 0.8, 1.0):
            for level in range(0, 101, 3):
                for goal in (0.5, 1, 2, 3, 4, 6, 8, 11):
                    state = PowerState(level=level, charging=False, health=health, temp_c=25, goal_hours=goal)
                    budget = level * health / goal
                    if GOAL_BASE_DRAIN > budget:
                        continue  # Essentials alone do not fit; nothing else can.
                    checked += 1
                    with self.subTest(level=level, health=health, goal=goal):
                        dec = Router()(state, DEFAULT_TASKS)
                        cost = sum(t.cost for t in DEFAULT_TASKS if t.name in dec.allowed and t.priority != Priority.ESSENTIAL)
                        self.assertLessEqual(GOAL_BASE_DRAIN + cost, budget + 1e-9)
                        # OPOS's own drain model agrees: with every saving step, the allowed set lasts to the goal.
                        drain = total_drain(DEFAULT_TASKS, paused=dec.deferred, steps=ALL_STEPS, health=health)
                        self.assertGreaterEqual(level / drain, goal - 1e-9)
                        # The final decision never allows more than the router funded.
                        tick = Runtime().step(0, level, health=health, temp_c=25, goal_hours=goal)
                        self.assertLessEqual(set(tick.decision.allowed), set(dec.allowed))
        self.assertGreater(checked, 100)

    def test_goal_funding_order(self):
        from opos.router import fund
        tasks = (Task("calls", Priority.ESSENTIAL, 0.5), Task("backup", Priority.OPTIONAL, 1),
                 Task("zeta", Priority.USEFUL, 2), Task("alpha", Priority.USEFUL, 2), Task("maps", Priority.USEFUL, 3))
        def allowed_with_spare(spare):
            goal = 2.0
            state = PowerState(level=(GOAL_BASE_DRAIN + spare + 1e-9) * goal, charging=False, health=1.0, temp_c=25, goal_hours=goal)
            return [n for n, (ok, _) in fund(state, tasks).items() if ok and n != "calls"]
        # Useful before optional, then lower cost first, then name; each funded cost is subtracted.
        self.assertEqual(allowed_with_spare(2), ["alpha"])
        self.assertEqual(sorted(allowed_with_spare(4)), ["alpha", "zeta"])
        self.assertEqual(sorted(allowed_with_spare(5)), ["alpha", "backup", "zeta"])
        self.assertEqual(sorted(allowed_with_spare(7)), ["alpha", "maps", "zeta"])
        self.assertEqual(sorted(allowed_with_spare(8)), ["alpha", "backup", "maps", "zeta"])

    def test_the_app_in_use_is_funded_first(self):
        from opos.router import fund
        tasks = DEFAULT_TASKS
        def verdicts(spare, first):
            goal = 2.0
            state = PowerState(level=(GOAL_BASE_DRAIN + spare + 1e-9) * goal, charging=False, health=1.0, temp_c=25, goal_hours=goal)
            return {n: ok for n, (ok, _) in fund(state, tasks, first).items() if n not in ("calls", "messages")}
        # Without an app in use: email (2), then maps (4), then photos backup (6).
        self.assertEqual(verdicts(7, ()), {"email": True, "maps": True, "photos backup": False})
        # Photos backup in use is funded first; the 1 left funds nothing else.
        self.assertEqual(verdicts(7, ("photos backup",)), {"email": False, "maps": False, "photos backup": True})
        # Maps in use does not fit 3, but its cost still comes off the budget: nothing else is funded.
        self.assertEqual(verdicts(3, ("maps",)), {"email": False, "maps": False, "photos backup": False})
        self.assertEqual(verdicts(3, ()), {"email": True, "maps": False, "photos backup": False})

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
