# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for profiles."""
import contextlib
import io
import unittest
from opos import evaluate
from opos import profiles
from opos.evaluate import EVAL_TASKS
from opos.governor import PowerState
from opos.router import Router
class TestProfiles(unittest.TestCase):
    def test_scenarios_deterministic(self):
        a = profiles.scenarios("fresh", 3, 7)
        b = profiles.scenarios("fresh", 3, 7)
        self.assertEqual(a, b)
    def test_scenarios_ranges(self):
        for name, cfg in profiles.PROFILES.items():
            scens = profiles.scenarios(name, 3, 42)
            for s in scens:
                self.assertGreaterEqual(s["level"], cfg["level"][0])
                self.assertLessEqual(s["level"], cfg["level"][1])
                self.assertGreaterEqual(s["health"], cfg["health"][0])
                self.assertLessEqual(s["health"], cfg["health"][1])
                self.assertGreaterEqual(s["temp"], cfg["temp"][0])
                self.assertLessEqual(s["temp"], cfg["temp"][1])
                self.assertEqual(s["r0"], cfg["r0"])
    def test_unknown_profile(self):
        with self.assertRaises(ValueError):
            profiles.scenarios("nope", 1, 0)
    def test_benchmark_rules(self):
        for name in profiles.PROFILES:
            res = profiles.benchmark(lambda: evaluate.rules_policy, name, n=3, seed=0)
            self.assertEqual(set(res.keys()), {"important_min", "useful_min", "missing_kept", "brownout"})
            self.assertEqual(res["missing_kept"], 0)
    def test_benchmark_router(self):
        for name in profiles.PROFILES:
            res = profiles.benchmark(lambda: evaluate.router_policy(), name, n=3, seed=1)
            self.assertEqual(set(res.keys()), {"important_min", "useful_min", "missing_kept", "brownout"})
            self.assertEqual(res["missing_kept"], 0)
    def test_main_lines(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = profiles.main(["--n", "2"])
        self.assertEqual(rc, 0)
        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 18)
        for line in lines:
            self.assertIn("important_min=", line)
    def test_router_monotonic(self):
        router = Router()
        levels = list(range(0, 101, 5))
        healths = [0.4, 0.7, 1.0]
        for health in healths:
            prev = None
            for lvl in levels:
                state = PowerState(lvl, False, health, 25)
                dec = router(state, EVAL_TASKS)
                cur = set(dec.deferred)
                if prev is not None:
                    self.assertTrue(cur.issubset(prev))
                prev = cur
    def test_router_essential(self):
        router = Router()
        for lvl in range(0, 101, 5):
            for health in [0.4, 0.7, 1.0]:
                state = PowerState(lvl, False, health, 25)
                dec = router(state, EVAL_TASKS)
                self.assertNotIn("calls", dec.deferred)
                self.assertNotIn("messages", dec.deferred)
if __name__ == "__main__":
    unittest.main()
