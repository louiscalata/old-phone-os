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
        self.assertEqual(r, {"level": 42.0, "temp_c": 31.5, "charging": False, "source": "termux-api", "charge_counter_uah": None})

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

    def test_charge_counter_kept(self):
        r = lb._termux_status(_run_json({"percentage": 100, "charge_counter": 3179000}), _which)
        self.assertEqual(r["charge_counter_uah"], 3179000)
        self.assertIsNone(lb._termux_status(_run_json({"percentage": 50, "charge_counter": -1}), _which)["charge_counter_uah"])


class CounterHealthTests(unittest.TestCase):
    def test_duo_backup_reading(self):
        # The Nov 4 Surface Duo capture: 3,179,000 uAh at 100% against the 3,577 mAh design.
        self.assertAlmostEqual(lb.counter_health(100, 3179000, 3577), 0.8887, places=3)
        self.assertAlmostEqual(lb.counter_health(50, 1589500, 3577), 0.8887, places=3)

    def test_low_charge_or_missing_inputs(self):
        self.assertIsNone(lb.counter_health(lb.MIN_ESTIMATE_PCT - 1, 900000, 3577))
        self.assertIsNone(lb.counter_health(80, None, 3577))
        self.assertIsNone(lb.counter_health(80, 2000000, None))
        self.assertEqual(lb.counter_health(50, 9000000, 3577), 1.0)

    def test_design_capacity_sources(self):
        def model(name):
            return lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=name + "\n", stderr="")
        self.assertEqual(lb.design_mah({}, model("Surface Duo")), 3577)
        self.assertEqual(lb.design_mah({"OPOS_BATTERY_DESIGN_MAH": "4000"}, model("Surface Duo")), 4000)
        self.assertIsNone(lb.design_mah({}, model("Pixel 3")))

    def test_read_live_prefers_counter_over_env(self):
        def run(cmd, **kw):
            out = "Surface Duo\n" if cmd[0] == "getprop" else json.dumps({"percentage": 80, "charge_counter": 2543200})
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        r = lb.read_live(env={"OPOS_BATTERY_HEALTH": "0.5"}, root="/nonexistent", run=run, which=_which)
        self.assertEqual(r["health_source"], "charge-counter")
        self.assertAlmostEqual(r["health"], 0.8887, places=3)


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


class OnPhoneLabelTests(unittest.TestCase):
    def test_termux_flag_names_the_phone(self):
        from opos import assist
        if assist.PHONE_BUILD:
            self.skipTest("packaged phone install")
        self.assertEqual(assist.local_route({}, free=4096).where, "on this computer")
        self.assertEqual(assist.local_route({"OPOS_ON_PHONE": "1"}, free=4096).where, "on this phone")


if __name__ == "__main__":
    unittest.main()
