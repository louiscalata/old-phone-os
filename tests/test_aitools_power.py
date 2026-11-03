# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import tempfile
import unittest
from pathlib import Path

from opos.aitools import power


def _put(supply: Path, name: str, files: dict[str, str]) -> Path:
    """Create supply entry with given files."""
    d = supply / name
    d.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        (d / k).write_text(v, encoding="utf-8")
    return d


class TestSupplyDirConstant(unittest.TestCase):
    def test_constant_value(self):
        """SUPPLY_DIR is default location."""
        self.assertEqual(power.SUPPLY_DIR, Path("/sys/class/power_supply"))


class TestOnExternalPower(unittest.TestCase):
    def test_missing_dir_returns_true(self):
        """Missing supply dir means desktop."""
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope"
            self.assertTrue(power.on_external_power(missing))

    def test_empty_dir_returns_true(self):
        """Empty dir has no battery."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            self.assertTrue(power.on_external_power(s))

    def test_no_battery_returns_true_even_mains_off(self):
        """No battery entry always true."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "AC", {"type": "Mains", "online": "0"})
            self.assertTrue(power.on_external_power(s))

    def test_file_not_dir_ignored(self):
        """Regular file in supply dir ignored."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            (s / "stray").write_text("hello", encoding="utf-8")
            self.assertTrue(power.on_external_power(s))

    def test_supply_is_file_returns_true(self):
        """Supply path is file treated as missing."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "file"
            p.write_text("x", encoding="utf-8")
            self.assertTrue(power.on_external_power(p))
            self.assertIsNone(power.battery_percent(p))

    def test_battery_charging_true(self):
        """Charging battery means external power."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Charging"})
            self.assertTrue(power.on_external_power(s))

    def test_battery_full_true(self):
        """Full battery means external power."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Full"})
            self.assertTrue(power.on_external_power(s))

    def test_battery_not_charging_true(self):
        """Not charging status counts as power."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Not charging"})
            self.assertTrue(power.on_external_power(s))

    def test_battery_discharging_false(self):
        """Discharging battery alone means battery."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Discharging"})
            self.assertFalse(power.on_external_power(s))

    def test_battery_status_stripped(self):
        """Whitespace around status ignored."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "  Charging\n"})
            self.assertTrue(power.on_external_power(s))

    def test_battery_status_case_sensitive(self):
        """Lowercase charging not counted."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "charging"})
            self.assertFalse(power.on_external_power(s))

    def test_battery_missing_status_false(self):
        """Missing status file yields no power."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery"})
            self.assertFalse(power.on_external_power(s))

    def test_battery_unreadable_status_false(self):
        """Unreadable status treated as missing."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            d = _put(s, "BAT0", {"type": "Battery"})
            (d / "status").unlink(missing_ok=True)
            (d / "status").mkdir()
            self.assertFalse(power.on_external_power(s))

    def test_mains_online_true_overrides_discharging(self):
        """Mains online 1 overrides discharging."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Discharging"})
            _put(s, "AC", {"type": "Mains", "online": "1"})
            self.assertTrue(power.on_external_power(s))

    def test_mains_online_zero_false(self):
        """Mains online 0 with discharging is battery."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Discharging"})
            _put(s, "AC", {"type": "Mains", "online": "0"})
            self.assertFalse(power.on_external_power(s))

    def test_mains_online_stripped(self):
        """Whitespace around online ignored."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Discharging"})
            _put(s, "AC", {"type": "Mains", "online": " 1 \n"})
            self.assertTrue(power.on_external_power(s))

    def test_non_battery_any_type_online(self):
        """Any non-battery type with online 1 counts."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Discharging"})
            _put(s, "USB", {"type": "USB", "online": "1"})
            self.assertTrue(power.on_external_power(s))

    def test_missing_type_with_online_counts(self):
        """Entry without type treated as non-battery."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Discharging"})
            _put(s, "MISC", {"online": "1"})
            self.assertTrue(power.on_external_power(s))

    def test_no_battery_missing_type_returns_true(self):
        """All missing type means no battery."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "FOO", {"online": "0"})
            self.assertTrue(power.on_external_power(s))

    def test_multiple_batteries_one_charging(self):
        """Any charging battery yields power."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Discharging"})
            _put(s, "BAT1", {"type": "Battery", "status": "Charging"})
            self.assertTrue(power.on_external_power(s))

    def test_multiple_mains_one_online(self):
        """Any mains online yields power."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "Discharging"})
            _put(s, "AC0", {"type": "Mains", "online": "0"})
            _put(s, "AC1", {"type": "Mains", "online": "1"})
            self.assertTrue(power.on_external_power(s))

    def test_garbage_status_false(self):
        """Unknown status not counted."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "FooBar"})
            self.assertFalse(power.on_external_power(s))

    def test_not_charging_space_exact(self):
        """Not charging requires exact spacing."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "status": "NotCharging"})
            self.assertFalse(power.on_external_power(s))


class TestBatteryPercent(unittest.TestCase):
    def test_missing_dir_none(self):
        """Missing dir yields None."""
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "gone"
            self.assertIsNone(power.battery_percent(missing))

    def test_empty_dir_none(self):
        """Empty supply yields None."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            self.assertIsNone(power.battery_percent(s))

    def test_no_battery_none(self):
        """No battery entry yields None."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "AC", {"type": "Mains", "online": "1", "capacity": "80"})
            self.assertIsNone(power.battery_percent(s))

    def test_valid_int(self):
        """Integer capacity parsed."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "42"})
            self.assertEqual(power.battery_percent(s), 42.0)

    def test_valid_float(self):
        """Float capacity parsed."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "42.5"})
            self.assertEqual(power.battery_percent(s), 42.5)

    def test_valid_with_whitespace(self):
        """Whitespace around capacity ignored."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "  60 \n"})
            self.assertEqual(power.battery_percent(s), 60.0)

    def test_boundary_zero(self):
        """Zero percent allowed."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "0"})
            self.assertEqual(power.battery_percent(s), 0.0)

    def test_boundary_hundred(self):
        """Hundred percent allowed."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "100"})
            self.assertEqual(power.battery_percent(s), 100.0)

    def test_missing_capacity_none(self):
        """Missing capacity file yields None."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery"})
            self.assertIsNone(power.battery_percent(s))

    def test_empty_capacity_none(self):
        """Empty capacity yields None."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": ""})
            self.assertIsNone(power.battery_percent(s))

    def test_whitespace_only_none(self):
        """Whitespace only capacity yields None."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "   \n"})
            self.assertIsNone(power.battery_percent(s))

    def test_garbage_none(self):
        """Garbage capacity yields None."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "abc"})
            self.assertIsNone(power.battery_percent(s))

    def test_percent_sign_none(self):
        """Capacity with percent sign rejected."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "50%"})
            self.assertIsNone(power.battery_percent(s))

    def test_out_of_range_low_none(self):
        """Negative capacity rejected."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "-1"})
            self.assertIsNone(power.battery_percent(s))

    def test_out_of_range_high_none(self):
        """Over 100 rejected."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "101"})
            self.assertIsNone(power.battery_percent(s))

    def test_out_of_range_float_high_none(self):
        """100.1 rejected."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "100.1"})
            self.assertIsNone(power.battery_percent(s))

    def test_nan_none(self):
        """NaN not in range."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "nan"})
            self.assertIsNone(power.battery_percent(s))

    def test_inf_none(self):
        """Inf not in range."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "inf"})
            self.assertIsNone(power.battery_percent(s))

    def test_capacity_is_directory_none(self):
        """Capacity path is directory treated as unreadable."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            d = _put(s, "BAT0", {"type": "Battery"})
            (d / "capacity").mkdir()
            self.assertIsNone(power.battery_percent(s))

    def test_multiple_batteries_sorted_first(self):
        """First sorted battery chosen."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT1", {"type": "Battery", "capacity": "90"})
            _put(s, "BAT0", {"type": "Battery", "capacity": "10"})
            self.assertEqual(power.battery_percent(s), 10.0)

    def test_multiple_batteries_first_invalid_none(self):
        """Invalid first battery not fallback."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "bad"})
            _put(s, "BAT1", {"type": "Battery", "capacity": "80"})
            self.assertIsNone(power.battery_percent(s))

    def test_multiple_batteries_first_out_of_range_none(self):
        """Out of range first battery yields None."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "200"})
            _put(s, "BAT1", {"type": "Battery", "capacity": "20"})
            self.assertIsNone(power.battery_percent(s))

    def test_ignores_non_battery_capacity(self):
        """Non-battery capacity ignored."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "AC", {"type": "Mains", "capacity": "99"})
            _put(s, "BAT0", {"type": "Battery", "capacity": "11"})
            self.assertEqual(power.battery_percent(s), 11.0)

    @unittest.skipIf(sys.platform == "win32", "POSIX sysfs power supply paths not available on Windows")
    def test_fake_path_isolation(self):
        """Battery and external power are read from the given supply directory."""
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "supply"
            s.mkdir()
            _put(s, "BAT0", {"type": "Battery", "capacity": "77", "status": "Charging"})
            self.assertTrue(power.on_external_power(s))
            self.assertEqual(power.battery_percent(s), 77.0)


if __name__ == "__main__":
    unittest.main()
