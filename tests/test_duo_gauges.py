# SPDX-License-Identifier: GPL-3.0-or-later
"""Duo pack level comes from the bq27742 gauges, not the PMIC node typed Battery."""
import tempfile
import unittest
from pathlib import Path

from opos.hw.battery import read_duo_gauges
from opos.hw.live_battery import read_live


def cell(root, name, now, full, design, temp, capacity):
    path = Path(root) / "sys/class/power_supply" / name
    path.mkdir(parents=True)
    (path / "type").write_text("BMS\n", encoding="utf-8")
    (path / "charge_now").write_text(str(now) + "\n", encoding="utf-8")
    (path / "charge_full").write_text(str(full) + "\n", encoding="utf-8")
    (path / "charge_full_design").write_text(str(design) + "\n", encoding="utf-8")
    (path / "temp").write_text(str(temp) + "\n", encoding="utf-8")
    (path / "capacity").write_text(str(capacity) + "\n", encoding="utf-8")
    (path / "status").write_text("Discharging\n", encoding="utf-8")


class DuoGauges(unittest.TestCase):
    def test_level_health_and_temperature_come_from_the_gauges(self):
        with tempfile.TemporaryDirectory() as tmp:
            pmic = Path(tmp) / "sys/class/power_supply/battery"
            pmic.mkdir(parents=True)
            (pmic / "type").write_text("Battery\n", encoding="utf-8")
            (pmic / "capacity").write_text("94\n", encoding="utf-8")
            (pmic / "status").write_text("Discharging\n", encoding="utf-8")
            # charge_full is µAh, charge_full_design is mAh. 850+860 over 1000+1000 is 85.5%.
            # Design 1200 mAh becomes 1_200_000 µAh, so health is 2_000_000/2_400_000, not 1.
            cell(tmp, "bq27742-0", 850_000, 1_000_000, 1200, 301, 85)
            cell(tmp, "bq27742-1", 860_000, 1_000_000, 1200, 311, 86)
            reading = read_live({}, tmp, run=lambda *a, **k: None, which=lambda name: None)
        self.assertIsNotNone(reading)
        self.assertEqual(reading["source"], "sysfs")
        self.assertAlmostEqual(reading["level"], 85.0, delta=1)
        self.assertNotEqual(reading["level"], 94.0)
        self.assertLess(reading["health"], 1.0)
        self.assertGreater(reading["health"], 0.8)
        self.assertAlmostEqual(reading["temp_c"], 30.6, places=1)
        self.assertEqual(reading["health_source"], "sysfs")

    def test_no_gauges_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_duo_gauges(tmp))


if __name__ == "__main__":
    unittest.main()
