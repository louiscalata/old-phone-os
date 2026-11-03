# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import io
import unittest
from unittest.mock import patch

import opos.evaleol
from opos.evaleol import basic_policy, benchmark_eol, main, report
from opos.evaluate import rules_policy, static_low_power


class TestBenchmarkEolAware(unittest.TestCase):
    def test_aware_improves_calls_and_avoids_brownout(self):
        for policy in (static_low_power, rules_policy):
            with self.subTest(policy=policy):
                unaware = benchmark_eol(lambda: policy, aware=False, n=8, seed=3)
                aware = benchmark_eol(lambda: policy, aware=True, n=8, seed=3)
                self.assertGreater(aware["calls"], unaware["calls"])
                self.assertEqual(aware["brownout_pct"], 0.0)
                self.assertGreaterEqual(unaware["brownout_pct"], 50)

    def test_aware_sms_and_alive_not_worse_for_rules_policy(self):
        unaware = benchmark_eol(lambda: rules_policy, aware=False, n=8, seed=3)
        aware = benchmark_eol(lambda: rules_policy, aware=True, n=8, seed=3)
        self.assertGreaterEqual(aware["sms"], unaware["sms"])
        self.assertGreaterEqual(aware["alive"], unaware["alive"])

    def test_f020_classifies_eol_termination(self):
        scenario = {"level": 0.5, "health": 0.35, "temp_c": 25}
        for aware in (False, True):
            with self.subTest(case="collapse", aware=aware):
                with patch("opos.evaleol.should_shutdown", return_value=False):
                    with patch("opos.sim.brownout", return_value=True):
                        res = opos.evaleol.run_eol(basic_policy, scenario, aware=aware, max_min=1)
                self.assertTrue(res["brownout"])
                self.assertEqual(res["shutdown_level"], 0.5)
                self.assertFalse(res["graceful"])
        for aware in (False, True):
            with self.subTest(case="exhaustion", aware=aware):
                with patch("opos.evaleol.should_shutdown", return_value=False):
                    with patch("opos.sim.brownout", return_value=False):
                        res = opos.evaleol.run_eol(basic_policy, scenario, aware=aware, tick_s=3600, max_min=1)
                self.assertFalse(res["brownout"])
                self.assertIsNone(res["shutdown_level"])
                self.assertFalse(res["graceful"])
        with patch("opos.evaleol.should_shutdown", return_value=True):
            with patch("opos.sim.brownout") as sim_brownout:
                res = opos.evaleol.run_eol(basic_policy, scenario, aware=True, max_min=1)
        self.assertFalse(res["brownout"])
        self.assertIsNone(res["shutdown_level"])
        self.assertTrue(res["graceful"])
        sim_brownout.assert_not_called()

    def test_benchmark_eol_deterministic(self):
        first = benchmark_eol(lambda: rules_policy, aware=True, n=8, seed=3)
        second = benchmark_eol(lambda: rules_policy, aware=True, n=8, seed=3)
        self.assertEqual(first, second)


class TestReportAndMain(unittest.TestCase):
    def test_report_returns_header_and_before_after_lines(self):
        lines = report(n=2, seed=1)
        self.assertEqual(len(lines), 9)
        for line in lines[1:]:
            self.assertTrue("before" in line or "after" in line, line)

    def test_main_prints_worn_battery_header_and_returns_zero(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = main(["--n", "2", "--seed", "1"])
        self.assertEqual(result, 0)
        output_lines = stream.getvalue().splitlines()
        self.assertTrue(output_lines)
        self.assertIn("worn battery", output_lines[0])


if __name__ == "__main__":
    unittest.main()
