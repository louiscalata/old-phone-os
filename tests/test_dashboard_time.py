# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import opos.dashboard
import opos.governor
import opos.runtime


class TestDashboardTimeHistory(unittest.TestCase):
    def make_dash(self, history_len=500):
        return opos.dashboard.Dashboard(opos.runtime.DEFAULT_TASKS, history_len=history_len)

    def test_minute_sampling_and_one_hour_window(self):
        d = self.make_dash(history_len=500)
        for i in range(200):
            d.record(100.0 - i, clock_s=i * 60)
        clock_s = 199 * 60
        self.assertLessEqual(len(d.history), 61)
        self.assertEqual(len(d.history), len(d._times))
        self.assertGreater(len(d.history), 0)
        self.assertGreaterEqual(d._times[0], clock_s - 3600)

    def test_history_and_times_equal_length(self):
        d = self.make_dash(history_len=10)
        self.assertEqual(len(d.history), len(d._times))
        for i in range(15):
            d.record(float(i), clock_s=i * 60)
            self.assertEqual(len(d.history), len(d._times))
        d.reset_history(42)
        self.assertEqual(len(d.history), len(d._times))
        d2 = self.make_dash(history_len=5)
        for i in range(8):
            d2.record(float(i), clock_s=None)
            self.assertEqual(len(d2.history), len(d2._times))

    def test_none_clock_appends_every_call_and_trims(self):
        d = self.make_dash(history_len=5)
        for i in range(8):
            d.record(float(i), clock_s=None)
        self.assertEqual(d.history, [3.0, 4.0, 5.0, 6.0, 7.0])
        self.assertEqual(len(d.history), len(d._times))
        self.assertTrue(all(t is None for t in d._times))

    def test_clock_backwards_resets_to_single_sample(self):
        d = self.make_dash(history_len=500)
        d.record(50, clock_s=7200)
        d.record(40, clock_s=100)
        self.assertNotIn(50, d.history)
        self.assertEqual(set(d.history), {40})
        self.assertEqual(len(d.history), len(d._times))

    def test_view_only_smooths_last_hour(self):
        d = self.make_dash(history_len=500)
        levels = [90, 80, 70, 60, 50, 40, 30, 20]
        for i, lvl in enumerate(levels):
            d.record(lvl, clock_s=i * 60)
        full_len = len(d.history)
        last_s = (len(levels) - 1) * 60
        v = d.view(20, opos.governor.Mode.NORMAL, False, 1.0, last_s + 4000, True, "")
        out = v["history"]
        self.assertIsInstance(out, list)
        self.assertLess(len(out), full_len)
        for val in out:
            self.assertGreaterEqual(val, min(levels))
            self.assertLessEqual(val, max(levels))

    def test_reset_history(self):
        d = self.make_dash(history_len=500)
        d.record(90, clock_s=0)
        d.record(80, clock_s=60)
        d.reset_history(50)
        self.assertEqual(d.history, [50])
        self.assertEqual(d._times, [None])


if __name__ == "__main__":
    unittest.main()
