# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from opos.hw import live_battery as lb
from opos.web import Phone


def _run_json(payload):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
    return run


def _which(name):
    return "/data/data/com.termux/files/usr/bin/" + name


def _no_which(name):
    return None


class TermuxTests(unittest.TestCase):
    def test_discharging(self):
        r = lb._termux_status(_run_json({"percentage": 42, "status": "DISCHARGING", "plugged": "UNPLUGGED", "temperature": 31.5}), _which)
        self.assertEqual(r, {"level": 42.0, "temp_c": 31.5, "charging": False, "source": "termux-api"})

    def test_charging_and_full(self):
        for status in ("CHARGING", "FULL"):
            r = lb._termux_status(_run_json({"percentage": 100, "status": status, "plugged": "PLUGGED_AC"}), _which)
            self.assertTrue(r["charging"], status)

    def test_missing_or_bad_output(self):
        self.assertIsNone(lb._termux_status(_run_json({}), _no_which))
        self.assertIsNone(lb._termux_status(_run_json({"status": "CHARGING"}), _which))
        def bad(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")
        self.assertIsNone(lb._termux_status(bad, _which))
        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 10)
        self.assertIsNone(lb._termux_status(boom, _which))

    def test_percentage_clamped(self):
        self.assertEqual(lb._termux_status(_run_json({"percentage": 140}), _which)["level"], 100.0)


class SysfsAndHealthTests(unittest.TestCase):
    def _root(self, files):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        base = Path(d.name) / "sys/class/power_supply/battery"
        base.mkdir(parents=True)
        for k, v in files.items():
            (base / k).write_text(v)
        return d.name

    def test_sysfs_reading_and_measured_health(self):
        root = self._root({"type": "Battery", "status": "Charging", "capacity": "57", "charge_full": "2400000", "charge_full_design": "3000000"})
        r = lb.read_live(env={}, root=root, which=_no_which)
        self.assertEqual((r["level"], r["charging"], r["source"]), (57.0, True, "sysfs"))
        self.assertAlmostEqual(r["health"], 0.8)
        self.assertEqual(r["health_source"], "sysfs")

    def test_health_env_then_assumed(self):
        root = self._root({"type": "Battery", "status": "Discharging", "capacity": "33"})
        self.assertEqual(lb._health({"OPOS_BATTERY_HEALTH": "0.62"}, root), (0.62, "OPOS_BATTERY_HEALTH"))
        self.assertEqual(lb._health({"OPOS_BATTERY_HEALTH": "x"}, root), (1.0, "assumed"))
        self.assertEqual(lb._health({}, root), (1.0, "assumed"))

    def test_nothing_readable(self):
        self.assertIsNone(lb.read_live(env={}, root=self._root({}), which=_no_which))


class LiveBatteryTests(unittest.TestCase):
    def _reading(self, level, charging=False):
        return {"level": level, "temp_c": 29.0, "charging": charging, "source": "termux-api", "health": 0.7, "health_source": "OPOS_BATTERY_HEALTH"}

    def test_refresh_is_rate_limited_and_advance_never_simulates(self):
        now = [0.0]
        readings = [self._reading(80), self._reading(79), self._reading(78)]
        b = lb.LiveBattery(reader=lambda: readings.pop(0), clock=lambda: now[0], refresh_s=15)
        self.assertEqual(b.level, 80)
        self.assertEqual(b.advance(3600, "NORMAL", (), per_hour=50), 80)  # no simulated drain
        now[0] = 16.0
        self.assertEqual(b.charge(1), 79)
        self.assertEqual(b.t_s, 16.0)

    def test_failed_read_keeps_last_values(self):
        seq = [self._reading(50), None]
        b = lb.LiveBattery(reader=lambda: seq.pop(0), clock=lambda: 0.0)
        self.assertTrue(b.ok)
        b.refresh(force=True)
        self.assertFalse(b.ok)
        self.assertEqual(b.level, 50)

    def test_phone_uses_live_reading(self):
        b = lb.LiveBattery(reader=lambda: self._reading(23, charging=True), clock=lambda: 0.0)
        phone = Phone(live_battery=b)
        s = phone.state()
        self.assertTrue(s["charging"])
        self.assertEqual(s["battery_source"], {"source": "termux-api", "health_source": "OPOS_BATTERY_HEALTH"})
        self.assertEqual(s["simulation"], {"modem": True})
        phone.tick(1)
        self.assertEqual(phone.sim.level, 23)

    def test_demo_phone_unchanged(self):
        s = Phone().state()
        self.assertNotIn("battery_source", s)
        self.assertFalse(s["charging"])


if __name__ == "__main__":
    unittest.main()
