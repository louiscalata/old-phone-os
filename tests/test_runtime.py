# SPDX-License-Identifier: GPL-3.0-or-later
import math
import unittest
from unittest import mock

from opos.budget import budget_for
from opos.governor import Decision, Mode, Priority
import opos.runtime as runtime_mod
from opos.runtime import Runtime


class TestRuntime(unittest.TestCase):
    def test_level5_allows_essential_defers_optional(self):
        rt = Runtime(visible=False)
        tick = rt.step(0, 5)
        self.assertIn('calls', tick.decision.allowed)
        self.assertIn('messages', tick.decision.allowed)
        self.assertIn('photos backup', tick.decision.deferred)
        self.assertIn('Calls', tick.screen)

    def test_falling_level_finite_hours_left(self):
        rt = Runtime(visible=False)
        t0 = rt.step(0, 50)
        t1 = rt.step(360, 49)
        t2 = rt.step(720, 48)
        t3 = rt.step(1080, 47)
        self.assertIsNotNone(t0.hours_left)
        self.assertTrue(math.isinf(t0.hours_left))
        self.assertIsNotNone(t3.hours_left)
        self.assertTrue(math.isfinite(t3.hours_left))
        self.assertGreater(t3.hours_left, 0)

    def test_budget_matches_mode_without_reserve(self):
        rt = Runtime(visible=False)
        tick = rt.step(0, 80, charging=False, health=1.0, temp_c=25.0)
        self.assertEqual(tick.decision.mode, Mode.NORMAL)
        self.assertEqual(tick.budget, budget_for(Mode.NORMAL, 25.0))
        self.assertEqual(tick.budget, budget_for(tick.decision.mode, 25.0))

    def test_normal_and_critical_budgets_differ(self):
        normal = budget_for(Mode.NORMAL, 25.0)
        critical = budget_for(Mode.CRITICAL, 25.0)
        self.assertNotEqual(normal, critical)

    def test_budget_reflects_final_critical_after_real_reserve(self):
        tasks = tuple(t for t in runtime_mod.DEFAULT_TASKS if t.priority == Priority.ESSENTIAL)
        original = runtime_mod.apply_reserve
        with mock.patch.object(runtime_mod, 'apply_reserve', wraps=original) as spy:
            rt = Runtime(visible=False, tasks=tasks)
            tick = rt.step(0, 31, charging=False, health=0.49, temp_c=-50.0)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args.args[0].mode, Mode.SAVER)
        self.assertEqual(tick.decision.mode, Mode.CRITICAL)
        self.assertEqual(tick.budget, budget_for(Mode.CRITICAL, -50.0))
        self.assertNotEqual(tick.budget, budget_for(Mode.SAVER, -50.0))

    def test_budget_calculation_receives_real_reserve_final_mode(self):
        tasks = tuple(t for t in runtime_mod.DEFAULT_TASKS if t.priority == Priority.ESSENTIAL)
        reserve_original = runtime_mod.apply_reserve
        budget_original = runtime_mod.budget_for
        with mock.patch.object(runtime_mod, 'apply_reserve', wraps=reserve_original) as reserve_spy:
            with mock.patch.object(runtime_mod, 'budget_for', wraps=budget_original) as budget_spy:
                rt = Runtime(visible=False, tasks=tasks)
                tick = rt.step(0, 31, charging=False, health=0.49, temp_c=-50.0)
        self.assertEqual(reserve_spy.call_count, 1)
        self.assertEqual(reserve_spy.call_args.args[0].mode, Mode.SAVER)
        self.assertEqual(tick.decision.mode, Mode.CRITICAL)
        self.assertEqual(budget_spy.call_count, 1)
        self.assertEqual(budget_spy.call_args.args, (Mode.CRITICAL, -50.0))
        self.assertEqual(tick.budget, budget_for(Mode.CRITICAL, -50.0))

    def test_budget_reflects_final_critical_after_mocked_reserve(self):
        def fake_reserve(decision, level, temp_c, health, tasks):
            return Decision(Mode.CRITICAL, decision.allowed, decision.deferred, decision.reason)

        with mock.patch.object(runtime_mod, 'apply_reserve', side_effect=fake_reserve) as spy:
            rt = Runtime(visible=False)
            tick = rt.step(0, 80, charging=False, health=1.0, temp_c=25.0)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args.args[0].mode, Mode.NORMAL)
        self.assertEqual(tick.decision.mode, Mode.CRITICAL)
        self.assertEqual(tick.budget, budget_for(Mode.CRITICAL, 25.0))
        self.assertNotEqual(tick.budget, budget_for(Mode.NORMAL, 25.0))

    def test_budget_invariant_across_modes(self):
        for temp_c in [25.0, 45.0, -5.0]:
            for mode in [Mode.NORMAL, Mode.SAVER, Mode.CRITICAL]:
                b = budget_for(mode, temp_c)
                self.assertIsNotNone(b)
        for level in [5, 20, 50, 80]:
            rt = Runtime(visible=False)
            tick = rt.step(0, level, charging=False, health=1.0, temp_c=25.0)
            self.assertEqual(tick.budget, budget_for(tick.decision.mode, 25.0))


if __name__ == '__main__':
    unittest.main()
