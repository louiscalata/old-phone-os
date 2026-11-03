# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import unittest
from pathlib import Path
from opos.battery import Sample, DrainEstimator, hours_left, essentials_reserve

class TestBattery(unittest.TestCase):
    def test_steady_rate(self):
        est = DrainEstimator(window=20, alpha=0.3)
        t = 0
        level = 100
        for _ in range(30):
            est.add(Sample(t, level))
            t += 360
            level -= 1
        self.assertAlmostEqual(est.rate(), 10.0, places=6)

    def test_older_sample_ignored(self):
        est = DrainEstimator(window=20, alpha=0.3)
        est.add(Sample(1000, 50))
        est.add(Sample(2000, 40))
        rate1 = est.rate()
        est.add(Sample(1500, 30))
        rate2 = est.rate()
        self.assertEqual(rate1, rate2)

    def test_charging_reset(self):
        est = DrainEstimator(window=20, alpha=0.3)
        est.add(Sample(0, 100))
        est.add(Sample(360, 99))
        self.assertIsNotNone(est.rate())
        est.add(Sample(720, 101))
        self.assertIsNone(est.rate())

    def test_hours_left(self):
        self.assertEqual(hours_left(50, 10), 5)

    def test_essentials_reserve_none(self):
        self.assertAlmostEqual(essentials_reserve(None), 0.7)

    def test_essentials_reserve_rate(self):
        self.assertAlmostEqual(essentials_reserve(10), 10.7)

class TestBatteryHw(unittest.TestCase):
    def test_present_excludes_absent(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ps = root / "sys/class/power_supply/BAT0"
            ps.mkdir(parents=True)
            (ps / "type").write_text("Battery")
            (ps / "present").write_text("0")
            (ps / "capacity").write_text("50")
            (ps / "status").write_text("Discharging")
            res = read_battery(root)
            self.assertFalse(res["present"])
            self.assertIsNone(res["status"])
            self.assertIsNone(res["capacity_pct"])
            self.assertIsNone(res["health"])

    def test_present_missing_preserves_telemetry(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ps = root / "sys/class/power_supply/BAT0"
            ps.mkdir(parents=True)
            (ps / "type").write_text("Battery")
            (ps / "capacity").write_text("50")
            (ps / "status").write_text("Discharging")
            res = read_battery(root)
            self.assertTrue(res["present"])
            self.assertEqual(res["status"], "Discharging")
            self.assertEqual(res["capacity_pct"], 50)

    def test_combined_capacity_missing_cap_uses_telemetry(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b0 = root / "sys/class/power_supply/BAT0"
            b0.mkdir(parents=True)
            (b0 / "type").write_text("Battery")
            (b0 / "capacity").write_text("100")
            (b0 / "charge_full").write_text("1000")
            b1 = root / "sys/class/power_supply/BAT1"
            b1.mkdir(parents=True)
            (b1 / "type").write_text("Battery")
            (b1 / "charge_now").write_text("0")
            (b1 / "charge_full").write_text("4000")
            res = read_battery(root)
            self.assertEqual(res["capacity_pct"], 20)

    def test_combined_capacity_weighted(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b0 = root / "sys/class/power_supply/BAT0"
            b0.mkdir(parents=True)
            (b0 / "type").write_text("Battery")
            (b0 / "capacity").write_text("100")
            (b0 / "charge_full").write_text("1000")
            b1 = root / "sys/class/power_supply/BAT1"
            b1.mkdir(parents=True)
            (b1 / "type").write_text("Battery")
            (b1 / "capacity").write_text("0")
            (b1 / "charge_full").write_text("4000")
            res = read_battery(root)
            self.assertEqual(res["capacity_pct"], 20)

    def test_direct_energy(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ps = root / "sys/class/power_supply/BAT0"
            ps.mkdir(parents=True)
            (ps / "type").write_text("Battery")
            (ps / "status").write_text("Discharging")
            (ps / "energy_now").write_text("3000")
            (ps / "energy_full").write_text("10000")
            res = read_battery(root)
            self.assertTrue(res["present"])
            self.assertEqual(res["capacity_pct"], 30)

    def test_direct_charge(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ps = root / "sys/class/power_supply/BAT0"
            ps.mkdir(parents=True)
            (ps / "type").write_text("Battery")
            (ps / "status").write_text("Discharging")
            (ps / "charge_now").write_text("750")
            (ps / "charge_full").write_text("1000")
            res = read_battery(root)
            self.assertTrue(res["present"])
            self.assertEqual(res["capacity_pct"], 75)

    def test_direct_energy_overreport_clamped(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ps = root / "sys/class/power_supply/BAT0"
            ps.mkdir(parents=True)
            (ps / "type").write_text("Battery")
            (ps / "status").write_text("Discharging")
            (ps / "energy_now").write_text("12000")
            (ps / "energy_full").write_text("10000")
            res = read_battery(root)
            self.assertTrue(res["present"])
            self.assertEqual(res["capacity_pct"], 100)

    def test_present_missing_capacity_no_direct(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ps = root / "sys/class/power_supply/BAT0"
            ps.mkdir(parents=True)
            (ps / "type").write_text("Battery")
            (ps / "status").write_text("Discharging")
            res = read_battery(root)
            self.assertTrue(res["present"])
            self.assertIsNone(res["capacity_pct"])

    def test_incomplete_weights_conservative_bound(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b0 = root / "sys/class/power_supply/BAT0"
            b0.mkdir(parents=True)
            (b0 / "type").write_text("Battery")
            (b0 / "capacity").write_text("100")
            b1 = root / "sys/class/power_supply/BAT1"
            b1.mkdir(parents=True)
            (b1 / "type").write_text("Battery")
            (b1 / "capacity").write_text("0")
            res = read_battery(root)
            self.assertTrue(res["present"])
            self.assertEqual(res["capacity_pct"], 0)

    def test_mixed_measurement_health_and_coverage(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b0 = root / "sys/class/power_supply/BAT0"
            b0.mkdir(parents=True)
            (b0 / "type").write_text("Battery")
            (b0 / "charge_full").write_text("80")
            (b0 / "charge_full_design").write_text("100")
            b1 = root / "sys/class/power_supply/BAT1"
            b1.mkdir(parents=True)
            (b1 / "type").write_text("Battery")
            (b1 / "energy_full").write_text("400")
            (b1 / "energy_full_design").write_text("1000")
            res = read_battery(root)
            self.assertAlmostEqual(res["health"], 0.6)
            self.assertAlmostEqual(res["health_coverage"], 1.0)
            (b0 / "energy_full").write_text("10")
            (b0 / "energy_full_design").write_text("100")
            res = read_battery(root)
            self.assertAlmostEqual(res["health"], 410.0 / 1100.0)
            b2 = root / "sys/class/power_supply/BAT2"
            b2.mkdir(parents=True)
            (b2 / "type").write_text("Battery")
            res = read_battery(root)
            self.assertAlmostEqual(res["health"], 410.0 / 1100.0)
            self.assertAlmostEqual(res["health_coverage"], 2.0 / 3.0)

    def test_unequal_capacity_health_weighted(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b0 = root / "sys/class/power_supply/BAT0"
            b0.mkdir(parents=True)
            (b0 / "type").write_text("Battery")
            (b0 / "charge_full").write_text("90")
            (b0 / "charge_full_design").write_text("100")
            b1 = root / "sys/class/power_supply/BAT1"
            b1.mkdir(parents=True)
            (b1 / "type").write_text("Battery")
            (b1 / "charge_full").write_text("0")
            (b1 / "charge_full_design").write_text("1")
            res = read_battery(root)
            self.assertAlmostEqual(res["health"], 0.9)
            self.assertAlmostEqual(res["health_coverage"], 0.5)

    def test_charge_health_validates_pairs(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        valid = [
            ("50000", "4000000", 0.0125),
            ("2000", "4000", 0.5),
            ("4000000", "4000000", 1.0),
        ]
        for full, design, expected in valid:
            with self.subTest(full=full, design=design):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    ps = root / "sys/class/power_supply/BAT0"
                    ps.mkdir(parents=True)
                    (ps / "type").write_text("Battery")
                    (ps / "charge_full").write_text(full)
                    (ps / "charge_full_design").write_text(design)
                    res = read_battery(root)
                    self.assertTrue(res["present"])
                    self.assertAlmostEqual(res["health"], expected)
                    self.assertAlmostEqual(res["health_coverage"], 1.0)
        invalid = [
            ("0", "4000000"),
            ("-1", "4000000"),
            ("4000001", "4000000"),
            ("2000", "0"),
            ("2000", "-1"),
        ]
        for full, design in invalid:
            with self.subTest(full=full, design=design):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    ps = root / "sys/class/power_supply/BAT0"
                    ps.mkdir(parents=True)
                    (ps / "type").write_text("Battery")
                    (ps / "charge_full").write_text(full)
                    (ps / "charge_full_design").write_text(design)
                    res = read_battery(root)
                    self.assertTrue(res["present"])
                    self.assertIsNone(res["health"])
                    self.assertAlmostEqual(res["health_coverage"], 0.0)

    def test_mixed_energy_preferred_no_voltage(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b0 = root / "sys/class/power_supply/BAT0"
            b0.mkdir(parents=True)
            (b0 / "type").write_text("Battery")
            (b0 / "charge_full").write_text("80")
            (b0 / "charge_full_design").write_text("100")
            (b0 / "energy_full").write_text("80")
            (b0 / "energy_full_design").write_text("100")
            b1 = root / "sys/class/power_supply/BAT1"
            b1.mkdir(parents=True)
            (b1 / "type").write_text("Battery")
            (b1 / "energy_full").write_text("40")
            (b1 / "energy_full_design").write_text("1000")
            res = read_battery(root)
            self.assertAlmostEqual(res["health"], 120.0 / 1100.0)
            self.assertAlmostEqual(res["health_coverage"], 1.0)

    def test_mixed_capacity_energy_preferred(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ps = root / "sys/class/power_supply/BAT0"
            ps.mkdir(parents=True)
            (ps / "type").write_text("Battery")
            (ps / "status").write_text("Discharging")
            (ps / "energy_now").write_text("3000")
            (ps / "energy_full").write_text("10000")
            (ps / "charge_now").write_text("750")
            (ps / "charge_full").write_text("1000")
            res = read_battery(root)
            self.assertTrue(res["present"])
            self.assertEqual(res["capacity_pct"], 30)

    def test_mixed_capacity_invalid_energy_falls_back_to_charge(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        cases = [None, "invalid", "0"]
        for energy_full in cases:
            with self.subTest(energy_full=energy_full):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    ps = root / "sys/class/power_supply/BAT0"
                    ps.mkdir(parents=True)
                    (ps / "type").write_text("Battery")
                    (ps / "status").write_text("Discharging")
                    (ps / "energy_now").write_text("3000")
                    if energy_full is not None:
                        (ps / "energy_full").write_text(energy_full)
                    (ps / "charge_now").write_text("750")
                    (ps / "charge_full").write_text("1000")
                    res = read_battery(root)
                    self.assertTrue(res["present"])
                    self.assertEqual(res["capacity_pct"], 75)

    def test_mixed_capacity_across_units_not_summed(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b0 = root / "sys/class/power_supply/BAT0"
            b0.mkdir(parents=True)
            (b0 / "type").write_text("Battery")
            (b0 / "charge_now").write_text("900")
            (b0 / "charge_full").write_text("1000")
            b1 = root / "sys/class/power_supply/BAT1"
            b1.mkdir(parents=True)
            (b1 / "type").write_text("Battery")
            (b1 / "energy_now").write_text("1000")
            (b1 / "energy_full").write_text("10000")
            res = read_battery(root)
            self.assertTrue(res["present"])
            self.assertNotEqual(res["capacity_pct"], 17)
            self.assertEqual(res["capacity_pct"], 10)

    def test_F028_unknown_topology_does_not_invent_pack_totals(self):
        import tempfile
        from pathlib import Path
        from opos.hw.battery import read_battery
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            specs = (("BAT0", "2000000", "3700000", "100000"), ("BAT1", "3000000", "3800000", "200000"))
            for name, full, volt, value in specs:
                psy = root / "sys/class/power_supply" / name
                psy.mkdir(parents=True)
                (psy / "type").write_text("Battery")
                (psy / "present").write_text("1")
                (psy / "status").write_text("Discharging")
                (psy / "charge_full").write_text(full)
                (psy / "voltage_now").write_text(volt)
                (psy / "resistance").write_text(value)
            res = read_battery(root)
            self.assertEqual(res, {"present": True, "status": "Discharging", "capacity_pct": None, "health": None, "health_coverage": 0.0})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            psy = root / "sys/class/power_supply/BAT0"
            psy.mkdir(parents=True)
            (psy / "type").write_text("Battery")
            (psy / "present").write_text("1")
            (psy / "status").write_text("Discharging")
            (psy / "charge_now").write_text("2400000")
            (psy / "charge_full").write_text("4000000")
            (psy / "charge_full_design").write_text("5000000")
            res = read_battery(root)
            self.assertEqual(res, {"present": True, "status": "Discharging", "capacity_pct": 60, "health": 0.8, "health_coverage": 1.0})

class TestNoSilentExceptPass(unittest.TestCase):
    def test_no_except_handler_with_only_pass(self):
        target = Path(__file__).resolve().parents[1] / "src/opos/hw/battery.py"
        tree = ast.parse(target.read_text(encoding="utf-8"))
        lines = sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        )
        self.assertEqual(lines, [], f"bare except-pass at lines {lines}")


if __name__ == "__main__":
    unittest.main()
