# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import unittest
from pathlib import Path
from opos.governor import PowerState, Mode, Priority, Task, Decision, rule_decide, Governor

class TestRuleDecide(unittest.TestCase):
    def test_normal_by_effective(self):
        state = PowerState(80, False, 1.0, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1), Task('u', Priority.USEFUL, 1), Task('o', Priority.OPTIONAL, 1))
        d = rule_decide(state, tasks)
        self.assertEqual(d.mode, Mode.NORMAL)
        self.assertEqual(len(d.allowed), 3)

    def test_normal_by_charging(self):
        state = PowerState(10, True, 1.0, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1),)
        d = rule_decide(state, tasks)
        self.assertEqual(d.mode, Mode.NORMAL)

    def test_saver_effective_40(self):
        state = PowerState(80, False, 0.5, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1), Task('u', Priority.USEFUL, 1), Task('o', Priority.OPTIONAL, 1))
        d = rule_decide(state, tasks)
        self.assertEqual(d.mode, Mode.SAVER)
        self.assertIn('e', d.allowed)
        self.assertIn('u', d.allowed)
        self.assertNotIn('o', d.allowed)

    def test_critical(self):
        state = PowerState(10, False, 1.0, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1), Task('u', Priority.USEFUL, 1))
        d = rule_decide(state, tasks)
        self.assertEqual(d.mode, Mode.CRITICAL)
        self.assertIn('e', d.allowed)
        self.assertNotIn('u', d.allowed)

    def test_temp_drop_normal_to_saver(self):
        state = PowerState(80, False, 1.0, 45)
        tasks = (Task('e', Priority.ESSENTIAL, 1),)
        d = rule_decide(state, tasks)
        self.assertEqual(d.mode, Mode.SAVER)

    def test_temp_drop_saver_to_critical(self):
        state = PowerState(80, False, 0.5, 45)
        tasks = (Task('e', Priority.ESSENTIAL, 1),)
        d = rule_decide(state, tasks)
        self.assertEqual(d.mode, Mode.CRITICAL)

    def test_essential_always_allowed(self):
        state = PowerState(5, False, 1.0, 50)
        tasks = (Task('e', Priority.ESSENTIAL, 1), Task('o', Priority.OPTIONAL, 1))
        d = rule_decide(state, tasks)
        self.assertTrue('e' in d.allowed)

    def test_order_kept(self):
        state = PowerState(80, False, 1.0, 25)
        t1 = Task('a', Priority.ESSENTIAL, 1)
        t2 = Task('b', Priority.USEFUL, 1)
        t3 = Task('c', Priority.OPTIONAL, 1)
        tasks = (t1, t2, t3)
        d = rule_decide(state, tasks)
        self.assertEqual(list(d.allowed), ['a', 'b', 'c'])

    def test_invalid_level(self):
        with self.assertRaises(ValueError):
            state = PowerState(-1, False, 1.0, 25)
            rule_decide(state, ())

    def test_invalid_health(self):
        with self.assertRaises(ValueError):
            state = PowerState(50, False, 1.5, 25)
            rule_decide(state, ())

    def test_reason_contains_rules(self):
        state = PowerState(50, False, 1.0, 25)
        d = rule_decide(state, ())
        self.assertIn('rules', d.reason.lower())

class TestGovernor(unittest.TestCase):
    def test_advisor_used(self):
        state = PowerState(50, False, 1.0, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1),)
        adv = lambda s, t: Decision(Mode.NORMAL, tuple(x.name for x in t), (), 'advisor')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertEqual(d.reason, 'advisor')

    def test_advisor_raises_fallback(self):
        state = PowerState(50, False, 1.0, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1),)
        def bad(s, t): raise RuntimeError()
        g = Governor(bad)
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())

    def test_advisor_none_fallback(self):
        state = PowerState(50, False, 1.0, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1),)
        g = Governor(lambda s, t: None)
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())

    def test_advisor_denies_essential_fallback(self):
        state = PowerState(50, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = (e, u)
        def deny(s, t): return Decision(Mode.NORMAL, (u.name,), (e.name,), 'bad')
        g = Governor(deny)
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())
        self.assertTrue('e' in d.allowed)

    def test_non_str_reason_falls_back_to_rules(self):
        state = PowerState(50, False, 1.0, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1),)
        adv = lambda s, t: Decision(Mode.NORMAL, tuple(x.name for x in t), (), ['x'])
        d = Governor(adv).decide(state, tasks)
        self.assertEqual(d.reason, 'rules')

    def test_str_reason_is_kept(self):
        state = PowerState(50, False, 1.0, 25)
        tasks = (Task('e', Priority.ESSENTIAL, 1),)
        baseline = rule_decide(state, tasks)
        adv = lambda s, t: Decision(baseline.mode, tuple(x.name for x in t), (), 'advisor')
        d = Governor(adv).decide(state, tasks)
        self.assertEqual(d.reason, 'advisor')

class TestGovernorAdvisorValidation(unittest.TestCase):
    def one_shot(self, tasks):
        for t in tasks:
            yield t

    def test_one_shot_consuming_advisor_exception_still_complete(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = [e, u, o]
        def adv(s, t):
            list(t)
            raise RuntimeError("boom")
        g = Governor(adv)
        d = g.decide(state, self.one_shot(tasks))
        self.assertIn('rules', d.reason.lower())
        self.assertEqual(set(d.allowed) | set(d.deferred), {'e', 'u', 'o'})
        self.assertIn('e', d.allowed)
        self.assertIsInstance(d.mode, Mode)

    def test_one_shot_none_collection_fallback_no_exception(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            list(t)
            return Decision(Mode.NORMAL, None, (), 'bad')
        g = Governor(adv)
        try:
            d = g.decide(state, self.one_shot(tasks))
        except Exception as exc:
            self.fail(f"exception escaped: {exc}")
        self.assertIn('rules', d.reason.lower())
        self.assertEqual(set(d.allowed) | set(d.deferred), {'e', 'u'})

    def test_one_shot_overlapping_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = [e, u, o]
        def adv(s, t):
            list(t)
            return Decision(Mode.NORMAL, ('e', 'u'), ('u', 'o'), 'bad')
        g = Governor(adv)
        d = g.decide(state, self.one_shot(tasks))
        self.assertIn('rules', d.reason.lower())
        self.assertEqual(set(d.allowed) & set(d.deferred), set())
        self.assertEqual(set(d.allowed) | set(d.deferred), {'e', 'u', 'o'})

    def test_one_shot_unknown_task_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            list(t)
            return Decision(Mode.NORMAL, ('e', 'ghost'), ('u',), 'bad')
        g = Governor(adv)
        d = g.decide(state, self.one_shot(tasks))
        self.assertIn('rules', d.reason.lower())
        self.assertNotIn('ghost', d.allowed)
        self.assertNotIn('ghost', d.deferred)
        self.assertEqual(set(d.allowed) | set(d.deferred), {'e', 'u'})

    def test_one_shot_missing_essential_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            list(t)
            return Decision(Mode.NORMAL, ('u',), ('e',), 'bad')
        g = Governor(adv)
        d = g.decide(state, self.one_shot(tasks))
        self.assertIn('rules', d.reason.lower())
        self.assertIn('e', d.allowed)
        self.assertNotIn('e', d.deferred)

    def test_one_shot_invalid_mode_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            list(t)
            return Decision("NORMAL", ('e', 'u'), (), 'bad')
        g = Governor(adv)
        try:
            d = g.decide(state, self.one_shot(tasks))
        except Exception as exc:
            self.fail(f"exception escaped: {exc}")
        self.assertIn('rules', d.reason.lower())
        self.assertIsInstance(d.mode, Mode)

    def test_one_shot_essential_deferred_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            list(t)
            return Decision(Mode.NORMAL, ('e', 'u'), ('e',), 'bad')
        g = Governor(adv)
        d = g.decide(state, self.one_shot(tasks))
        self.assertIn('rules', d.reason.lower())
        self.assertNotIn('e', d.deferred)

    def test_one_shot_incomplete_coverage_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = [e, u, o]
        def adv(s, t):
            list(t)
            return Decision(Mode.NORMAL, ('e',), ('u',), 'bad')
        g = Governor(adv)
        d = g.decide(state, self.one_shot(tasks))
        self.assertIn('rules', d.reason.lower())
        self.assertEqual(set(d.allowed) | set(d.deferred), {'e', 'u', 'o'})

    def test_one_shot_valid_advisor_is_used(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = [e, u, o]
        def adv(s, t):
            first = tuple(x.name for x in t)
            second = tuple(x.name for x in t)
            return Decision(Mode.NORMAL, second, (), 'advisor')
        g = Governor(adv)
        d = g.decide(state, self.one_shot(tasks))
        self.assertEqual(d.reason, 'advisor')
        self.assertEqual(set(d.allowed), {'e', 'u', 'o'})
        self.assertEqual(d.deferred, ())
        self.assertIsInstance(d.mode, Mode)

    def test_advisor_none_deferred_fallback(self):
        state = PowerState(50, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        tasks = [e]
        def adv(s, t):
            return Decision(Mode.NORMAL, ('e',), None, 'bad')
        g = Governor(adv)
        try:
            d = g.decide(state, tasks)
        except Exception as exc:
            self.fail(f"exception escaped: {exc}")
        self.assertIn('rules', d.reason.lower())

    def test_advisor_string_collection_fallback(self):
        state = PowerState(50, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        tasks = [e]
        def adv(s, t):
            return Decision(Mode.NORMAL, "e", (), 'bad')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())

    def test_duplicate_allowed_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            return Decision(Mode.NORMAL, ('e', 'e'), ('u',), 'bad')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())

    def test_duplicate_deferred_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            return Decision(Mode.NORMAL, ('e',), ('u', 'u'), 'bad')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())

    def test_dict_collection_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            return Decision(Mode.NORMAL, {e.name: e.name, u.name: u.name}, (), 'bad')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())

    def test_generator_collection_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        def adv(s, t):
            return Decision(Mode.NORMAL, (x for x in ('e', 'u')), (), 'bad')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())

    def test_dict_deferred_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        baseline = Decision(Mode.NORMAL, ('e',), ('u',), 'bad')
        g = Governor(lambda s, t: baseline)
        d = g.decide(state, tasks)
        self.assertEqual(d, baseline)
        self.assertEqual(d.reason, 'bad')
        completed = False
        def adv(s, t):
            nonlocal completed
            result = Decision(Mode.NORMAL, ('e',), {u.name: u.name}, 'bad')
            completed = True
            return result
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertTrue(completed)
        self.assertIn('rules', d.reason.lower())

    def test_generator_deferred_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        baseline = Decision(Mode.NORMAL, ('e',), ('u',), 'bad')
        g = Governor(lambda s, t: baseline)
        d = g.decide(state, tasks)
        self.assertEqual(d, baseline)
        self.assertEqual(d.reason, 'bad')
        completed = False
        def adv(s, t):
            nonlocal completed
            result = Decision(Mode.NORMAL, ('e',), (x for x in ('u',)), 'bad')
            completed = True
            return result
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertTrue(completed)
        self.assertIn('rules', d.reason.lower())

    def test_lookalike_decision_fallback(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = [e, u]
        class Lookalike:
            def __init__(self):
                self.mode = Mode.NORMAL
                self.allowed = ('e', 'u')
                self.deferred = ()
                self.reason = 'advisor'
        g = Governor(lambda s, t: Lookalike())
        d = g.decide(state, tasks)
        self.assertIn('rules', d.reason.lower())

    def test_valid_list_deferred_accepted(self):
        state = PowerState(80, False, 0.5, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = (e, u, o)
        def adv(s, t):
            return Decision(Mode.SAVER, ('e', 'u'), ['o'], 'advisor')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertEqual(d.reason, 'advisor')
        self.assertEqual(set(d.allowed), {'e', 'u'})
        self.assertEqual(set(d.deferred), {'o'})

    def test_valid_set_deferred_accepted(self):
        state = PowerState(80, False, 0.5, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = (e, u, o)
        def adv(s, t):
            return Decision(Mode.SAVER, ('e', 'u'), {'o'}, 'advisor')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertEqual(d.reason, 'advisor')
        self.assertEqual(set(d.allowed), {'e', 'u'})
        self.assertEqual(set(d.deferred), {'o'})

class TestThermalPolicyEnforcement(unittest.TestCase):
    def test_charging_high_temp_advisor_cannot_relax(self):
        state = PowerState(10, True, 1.0, 50)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = (e, u, o)
        baseline = rule_decide(state, tasks)
        self.assertEqual(baseline.mode, Mode.SAVER)
        self.assertIn('e', baseline.allowed)
        self.assertIn('u', baseline.allowed)
        self.assertNotIn('o', baseline.allowed)
        adv = lambda s, t: Decision(Mode.NORMAL, tuple(x.name for x in t), (), 'advisor')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertEqual(d.mode, baseline.mode)
        self.assertEqual(set(d.allowed), set(baseline.allowed))
        self.assertEqual(set(d.deferred), set(baseline.deferred))
        self.assertNotIn('o', d.allowed)
        self.assertIn('o', d.deferred)
        self.assertIn('e', d.allowed)
        self.assertTrue(set(d.allowed).issubset(set(baseline.allowed)))
        self.assertTrue(set(baseline.deferred).issubset(set(d.deferred)))
        self.assertIn('rules', d.reason.lower())

    def test_high_temp_saver_to_critical_cannot_relax(self):
        state = PowerState(80, False, 0.5, 45)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = (e, u, o)
        baseline = rule_decide(state, tasks)
        self.assertEqual(baseline.mode, Mode.CRITICAL)
        self.assertEqual(set(baseline.allowed), {'e'})
        adv = lambda s, t: Decision(Mode.NORMAL, ('e', 'u', 'o'), (), 'advisor')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertEqual(d.mode, Mode.CRITICAL)
        self.assertEqual(set(d.allowed), {'e'})
        self.assertNotIn('u', d.allowed)
        self.assertNotIn('o', d.allowed)
        self.assertIn('u', d.deferred)
        self.assertIn('o', d.deferred)
        self.assertIn('rules', d.reason.lower())

    def test_cool_advisor_preserved_no_over_constrain(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = (e, u, o)
        baseline = rule_decide(state, tasks)
        self.assertEqual(baseline.mode, Mode.NORMAL)
        self.assertEqual(set(baseline.allowed), {'e', 'u', 'o'})
        adv = lambda s, t: Decision(Mode.NORMAL, tuple(x.name for x in t), (), 'advisor')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertEqual(d.mode, Mode.NORMAL)
        self.assertEqual(set(d.allowed), {'e', 'u', 'o'})
        self.assertEqual(d.deferred, ())
        self.assertIn('o', d.allowed)
        self.assertEqual(d.reason, 'advisor')

    def test_invalid_level_advisor_blocked(self):
        state = PowerState(-1, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        tasks = (e,)
        adv = lambda s, t: Decision(Mode.NORMAL, (e.name,), (), 'advisor')
        g = Governor(adv)
        with self.assertRaises(ValueError):
            g.decide(state, tasks)

    def test_invalid_health_advisor_blocked(self):
        state = PowerState(50, False, 1.5, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        tasks = (e,)
        adv = lambda s, t: Decision(Mode.NORMAL, (e.name,), (), 'advisor')
        g = Governor(adv)
        with self.assertRaises(ValueError):
            g.decide(state, tasks)

    def test_invalid_state_cool_advisor_still_blocked(self):
        state = PowerState(50, False, 1.5, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        tasks = (e, u)
        baseline_tasks = (e, u)
        with self.assertRaises(ValueError):
            rule_decide(state, baseline_tasks)
        adv = lambda s, t: Decision(Mode.NORMAL, tuple(x.name for x in t), (), 'advisor')
        g = Governor(adv)
        with self.assertRaises(ValueError):
            g.decide(state, tasks)

    def test_cool_advisor_stricter_saver_preserved(self):
        state = PowerState(80, False, 1.0, 25)
        e = Task('e', Priority.ESSENTIAL, 1)
        u = Task('u', Priority.USEFUL, 1)
        o = Task('o', Priority.OPTIONAL, 1)
        tasks = (e, u, o)
        baseline = rule_decide(state, tasks)
        self.assertEqual(baseline.mode, Mode.NORMAL)
        self.assertEqual(set(baseline.allowed), {'e', 'u', 'o'})
        adv = lambda s, t: Decision(Mode.SAVER, ('e', 'u'), ('o',), 'advisor')
        g = Governor(adv)
        d = g.decide(state, tasks)
        self.assertEqual(d.mode, Mode.SAVER)
        self.assertEqual(set(d.allowed), {'e', 'u'})
        self.assertNotIn('o', d.allowed)
        self.assertIn('o', d.deferred)
        self.assertEqual(d.reason, 'advisor')

class TestNoSilentExceptPass(unittest.TestCase):
    def test_no_except_handler_with_only_pass(self):
        src = Path(__file__).resolve().parents[1] / "src" / "opos" / "governor.py"
        tree = ast.parse(src.read_text())
        lines = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and len(n.body) == 1 and isinstance(n.body[0], ast.Pass)]
        self.assertEqual(lines, [], f"except-pass at lines {lines}")

if __name__ == '__main__':
    unittest.main()
