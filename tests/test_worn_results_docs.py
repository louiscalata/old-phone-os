# SPDX-License-Identifier: GPL-3.0-or-later;
import unittest
from pathlib import Path

DOC_PATH = Path('docs/worn-battery-results.md')

class TestWornResultsDocs(unittest.TestCase):
    def setUp(self):
        self.text = DOC_PATH.read_text(encoding='utf-8')

    def test_version_label_present(self):
        self.assertIn('Results v1:', self.text)

    def test_old_results_heading_removed(self):
        self.assertNotIn('Results:\n~~~', self.text)

    def test_event_definition_present(self):
        self.assertIn('Event definition:', self.text)
        self.assertIn('brownout =', self.text)

    def test_planned_shutdown_accounting_present(self):
        self.assertIn('Planned-shutdown accounting:', self.text)

    def test_resistance_assumptions_present(self):
        self.assertIn('Tested resistance assumptions:', self.text)

    def test_controlled_shutdown_qualification(self):
        lower = self.text.lower()
        self.assertIn("neither zero brownouts% nor the simulator's termination flag "
                      "demonstrates controlled shutdown on real hardware.", lower)
        self.assertIn('controlled shutdown', lower)

    def test_how_to_read_qualified(self):
        self.assertIn('Zero brownouts% in this simulation does not by itself demonstrate controlled shutdown on real hardware.', self.text)

    def test_table_numbers_preserved(self):
        rows = [
            'basic   before     0.76  0.76     0.76  0.76    0.76        100     24.6',
            'basic   after      1.91  2.03     2.03  1.65    2.03          0      0.0',
            'static  before     1.62  1.62     1.62  1.62    1.62         98     20.5',
            'static  after      2.74  2.86     2.86  2.48    2.86          0      0.0',
            'rules   before     3.67  3.67     3.67  3.67    3.67        100     18.8',
            'rules   after      4.73  4.84     4.84  4.49    4.84          0      0.0',
            'router  before     5.52  5.52     5.52  5.52    5.52        100     18.7',
            'router  after      6.54  6.69     6.69  6.31    6.69          0      0.0',
        ]
        for row in rows:
            self.assertIn(row, self.text)

    def test_brownout_threshold_matches_simulator(self):
        import opos.eol as eol
        eol_text = Path('src/opos/eol.py').read_text(encoding='utf-8')
        self.assertEqual(eol.CUTOFF_V, 3.0)
        self.assertIn(f"{eol.CUTOFF_V:.1f} V", self.text)
        self.assertNotIn("3.2 V", self.text)
        self.assertNotIn("200 ms", self.text)
        self.assertNotIn("≥ 200", self.text)
        self.assertIn("CUTOFF_V = 3.0", eol_text)
        self.assertIn("3.0 V", self.text)

    def test_resistance_assumptions_match_simulator(self):
        def norm(s):
            return "".join(s.split())
        text_norm = norm(self.text)
        self.assertIn(norm("r0_for_health = 0.05 + 0.23*(1-health)"), text_norm)
        self.assertIn(norm("temperature factor 1+0.03*(25-temp) below 25°C and 1+0.02*(temp-35) above 35°C"), text_norm)
        self.assertIn(norm("SoC factor 1+3*(1-q/100)^3"), text_norm)
        self.assertNotIn("2.5", self.text)
        self.assertNotIn("0.004", self.text)

    def test_resistance_model_documented_values(self):
        import opos.eol as eol
        health_table = [(1.0, 0.05), (0.35, 0.1995), (0.05, 0.2685)]
        charge_table = [(0, 4.0), (50, 1.375), (100, 1.0)]
        temp_table = [(0, 1.75), (25, 1.0), (30, 1.0), (35, 1.0), (45, 1.2)]
        for health, expected_base in health_table:
            with self.subTest(health=health):
                actual_base = eol.r0_for_health(health)
                self.assertAlmostEqual(actual_base, expected_base, places=7)
            for q, expected_charge_factor in charge_table:
                for temp, expected_temp_factor in temp_table:
                    with self.subTest(health=health, q=q, temp=temp):
                        actual_resistance = eol.resistance(q, temp, actual_base)
                        expected_resistance = expected_base * expected_charge_factor * expected_temp_factor
                        self.assertAlmostEqual(actual_resistance, expected_resistance, places=7)

    def test_planned_shutdown_accounting_matches_simulator(self):
        import opos.eol as eol
        doc = self.text
        self.assertNotIn("reports average state-of-charge at planned shutdown", doc)
        self.assertIn("died_at% is 0.0", doc)
        self.assertIn("brownouts% excludes planned shutdowns", doc)
        self.assertIn("shutdown_floor", doc)
        self.assertTrue(hasattr(eol, "shutdown_floor"))
        self.assertTrue(hasattr(eol, "call_reserve"))
        self.assertIn("neither zero brownouts% nor the simulator's termination flag "
                      "demonstrates controlled shutdown on real hardware.", doc.lower())

    def test_event_classification_and_shutdown_accounting(self):
        from unittest.mock import patch
        import opos.eol as eol
        from opos.evaleol import basic_policy, run_eol, benchmark_eol
        brownout_scenario = {"level": 5.0, "health": 0.35, "temp_c": 0.0}
        brownout_result = run_eol(basic_policy, brownout_scenario, aware=False, max_min=1)
        self.assertTrue(brownout_result["brownout"])
        self.assertFalse(brownout_result["graceful"])
        self.assertEqual(brownout_result["shutdown_level"], 5.0)
        floor = eol.shutdown_floor(-30.0, eol.r0_for_health(0.35))
        self.assertGreater(floor, 0)
        planned_scenario = {"level": floor / 2, "health": 0.35, "temp_c": -30.0}
        planned_result = run_eol(basic_policy, planned_scenario, aware=True, max_min=1)
        self.assertTrue(planned_result["graceful"])
        self.assertFalse(planned_result["brownout"])
        self.assertIsNone(planned_result["shutdown_level"])
        self.assertEqual(planned_result["alive_h"], 0.0)
        with patch("opos.evaleol.scenarios_eol", return_value=[brownout_scenario]):
            brownout_bench = benchmark_eol(lambda: basic_policy, aware=False, n=1)
        self.assertEqual(brownout_bench["brownout_pct"], 100.0)
        self.assertEqual(brownout_bench["graceful_pct"], 0.0)
        self.assertEqual(brownout_bench["shutdown_level"], 5.0)
        with patch("opos.evaleol.scenarios_eol", return_value=[planned_scenario]):
            planned_bench = benchmark_eol(lambda: basic_policy, aware=True, n=1)
        self.assertEqual(planned_bench["brownout_pct"], 0.0)
        self.assertEqual(planned_bench["graceful_pct"], 100.0)
        self.assertEqual(planned_bench["shutdown_level"], 0.0)

    def test_benchmark_event_percentages_mixed_batch(self):
        from unittest.mock import patch
        from opos.evaleol import benchmark_eol, basic_policy, run_eol as real_run_eol

        sample_result = real_run_eol(basic_policy, {"level": 100.0, "health": 1.0, "temp_c": 25.0}, aware=False, max_min=1)

        def make_result(brownout, graceful, shutdown_level, alive_h):
            r = dict(sample_result)
            r["brownout"] = brownout
            r["graceful"] = graceful
            r["shutdown_level"] = shutdown_level
            r["alive_h"] = alive_h
            return r

        results = [
            make_result(True, False, 5.0, 0.0),
            make_result(False, True, None, 0.0),
            make_result(False, True, None, 0.0),
            make_result(False, False, None, 12.3),
            make_result(False, False, None, 15.7),
            make_result(False, False, None, 9.1),
        ]

        scenarios = [
            {"level": 1.0, "health": 0.1, "temp_c": 0.0},
            {"level": 2.0, "health": 0.2, "temp_c": 0.0},
            {"level": 3.0, "health": 0.3, "temp_c": 0.0},
            {"level": 4.0, "health": 0.4, "temp_c": 0.0},
            {"level": 5.0, "health": 0.5, "temp_c": 0.0},
            {"level": 6.0, "health": 0.6, "temp_c": 0.0},
        ]

        with patch("opos.evaleol.scenarios_eol", return_value=scenarios):
            with patch("opos.evaleol.run_eol", side_effect=results) as mock_run:
                bench = benchmark_eol(lambda: basic_policy, aware=False, n=6)

        self.assertEqual(mock_run.call_count, 6)

        expected_brownout_pct = 1 / 6 * 100
        expected_graceful_pct = 2 / 6 * 100

        self.assertAlmostEqual(bench["brownout_pct"], expected_brownout_pct)
        self.assertAlmostEqual(bench["graceful_pct"], expected_graceful_pct)

    def test_event_classification_no_terminal_event(self):
        from opos.evaleol import basic_policy, run_eol
        scenario = {"level": 100.0, "health": 1.0, "temp_c": 25.0}
        for aware in (False, True):
            result = run_eol(basic_policy, scenario, aware=aware, max_min=1)
            self.assertFalse(result["brownout"])
            self.assertFalse(result["graceful"])

if __name__ == '__main__':
    unittest.main()
