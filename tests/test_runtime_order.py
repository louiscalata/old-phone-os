# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import subprocess
import unittest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = os.path.join(root, "src")
if src not in sys.path:
    sys.path.insert(0, src)

from opos.runtime import Runtime, DEFAULT_TASKS
from opos.reserve import reserve_level, WORN_REASON
from opos.governor import Mode


class TestRuntimeOrder(unittest.TestCase):
    def test_order_is_independent_of_hash_seed(self):
        seeds = ("0", "1", "2", "3")
        outputs = []
        code = "from opos.runtime import Runtime; r=Runtime(); d=r.step(0.0,25.0).decision; print(repr((d.allowed,d.deferred)))"
        for seed in seeds:
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = seed
            env["PYTHONPATH"] = src
            result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
            outputs.append(result.stdout.strip())
        first = outputs[0]
        for out in outputs[1:]:
            self.assertEqual(first, out)

    def test_allowed_and_deferred_follow_task_order(self):
        r = Runtime()
        decision = r.step(0.0, 25.0).decision
        names = []
        for t in DEFAULT_TASKS:
            name = getattr(t, "name", t)
            names.append(name)
        index = {n: i for i, n in enumerate(names)}
        allowed = decision.allowed
        deferred = decision.deferred
        for i in range(len(allowed) - 1):
            self.assertLess(index[allowed[i]], index[allowed[i + 1]])
        for i in range(len(deferred) - 1):
            self.assertLess(index[deferred[i]], index[deferred[i + 1]])

    def test_worn_battery_keeps_reserve(self):
        level = reserve_level(25.0, 0.35) + 2
        r = Runtime()
        decision = r.step(0.0, level, health=0.35, temp_c=25.0).decision
        self.assertEqual(decision.mode, Mode.CRITICAL)
        self.assertEqual(decision.reason, WORN_REASON)
        self.assertIn("calls", decision.allowed)
        self.assertIn("messages", decision.allowed)
        self.assertNotIn("email", decision.allowed)
        self.assertNotIn("photos backup", decision.allowed)

    def test_healthy_battery_unchanged_by_reserve(self):
        level = reserve_level(25.0, 0.35) + 2
        r = Runtime()
        decision = r.step(0.0, level, health=1.0, temp_c=25.0).decision
        self.assertNotEqual(decision.reason, WORN_REASON)

    def test_charging_skips_reserve(self):
        level = reserve_level(25.0, 0.35) + 2
        r = Runtime()
        decision = r.step(0.0, level, health=0.35, temp_c=25.0, charging=True).decision
        self.assertNotEqual(decision.reason, WORN_REASON)
