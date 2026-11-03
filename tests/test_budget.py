# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.budget import Budget, BUDGETS, budget_for, allows_app
from opos.governor import Mode

class TestBudget(unittest.TestCase):
    def test_budgets_normal(self):
        self.assertEqual(BUDGETS[Mode.NORMAL], Budget(80, 70, 15, "full"))
    def test_budgets_saver(self):
        self.assertEqual(BUDGETS[Mode.SAVER], Budget(40, 40, 60, "full"))
    def test_budgets_critical(self):
        self.assertEqual(BUDGETS[Mode.CRITICAL], Budget(10, 20, 0, "calls-sms"))
    def test_budget_for_no_temp(self):
        self.assertEqual(budget_for(Mode.SAVER, 20), BUDGETS[Mode.SAVER])
    def test_budget_for_hot(self):
        self.assertEqual(budget_for(Mode.NORMAL, 45), Budget(40, 30, 15, "full"))
    def test_budget_for_very_hot(self):
        b = budget_for(Mode.CRITICAL, 55)
        self.assertEqual(b.cpu_pct, 5)
        self.assertEqual(b.screen_brightness, 20)
        self.assertEqual(b.sync_minutes, 0)
        self.assertEqual(b.radio, "calls-sms")
    def test_allows_app_essential(self):
        self.assertTrue(allows_app(BUDGETS[Mode.CRITICAL], True))
    def test_allows_app_non_essential(self):
        self.assertFalse(allows_app(BUDGETS[Mode.CRITICAL], False))
        self.assertTrue(allows_app(BUDGETS[Mode.NORMAL], False))

if __name__ == "__main__":
    unittest.main()
