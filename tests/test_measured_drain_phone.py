# SPDX-License-Identifier: GPL-3.0-or-later
"""Measured drain in the web Phone: a live phone's time left and goal estimate follow its real discharge, not the model."""
import json
import unittest
from unittest import mock

from opos.battery import DrainEstimator, Sample, essentials_reserve
from opos.hw.live_battery import LiveBattery
from opos.power import time_left_words
from opos.web import Phone


class PhoneDrainTests(unittest.TestCase):
    """Phone(live_battery=...) drives its estimates from measured discharge; Phone() does not change."""

    def setUp(self):
        self.now = [1000.0]
        self.true = [50.0]
        self.charging = [False]
        self.per_hour = [5.0]
        patch = mock.patch("opos.web._local_clock_s", return_value=43200)  # noon, so a goal of 18:00 is six hours away
        patch.start()
        self.addCleanup(patch.stop)

    def reader(self):
        return {"level": round(self.true[0]), "temp_c": 29.0, "charging": self.charging[0], "source": "sysfs",
                "health": 0.7, "health_source": "OPOS_BATTERY_HEALTH"}

    def phone(self, **kw):
        b = LiveBattery(reader=self.reader, clock=lambda: self.now[0], refresh_s=15)
        p = Phone(live_battery=b, **kw)
        return p

    def poll(self, p, seconds):
        s = None
        end = self.now[0] + seconds
        while self.now[0] < end - 1e-9:
            self.now[0] += 15
            self.true[0] -= self.per_hour[0] * 15 / 3600
            s = p.state()
        return s

    def test_time_left_and_goal_come_from_the_measurement_once_it_is_trusted(self):
        p = self.phone()
        p.dash.set_goal("18:00", p.clock_s)
        s = p.state()
        model = p._tick_obj.drain_per_h
        self.assertLess(self.true[0] / model, 6.0)  # at the model's rate this phone misses the goal
        self.assertEqual(s["battery"]["drain"]["basis"], "model")
        self.assertEqual(s["battery"]["drain"]["per_hour"], round(model, 2))
        self.assertFalse(s["battery"]["goal_met"])
        self.assertEqual(s["battery"]["time_left"], time_left_words(round(self.true[0]) / model))
        s = self.poll(p, 3600)
        drain = s["battery"]["drain"]
        self.assertEqual(drain["basis"], "measured")
        self.assertAlmostEqual(drain["per_hour"], 5.0, delta=0.3)
        self.assertEqual(drain["model_per_hour"], round(model, 2))
        level = s["battery"]["percent"]
        self.assertAlmostEqual(s["battery"]["hours_left"], level / drain["per_hour"], places=1)
        self.assertEqual(s["battery"]["time_left"], time_left_words(s["battery"]["hours_left"]))
        self.assertTrue(s["battery"]["goal_met"])  # about 9 h at 5 %/h against a goal six hours away
        self.assertEqual(s["battery_source"], {"source": "sysfs", "health_source": "OPOS_BATTERY_HEALTH"})
        json.dumps(s)

    def test_goal_is_missed_when_the_measured_drain_is_fast(self):
        p = self.phone()
        p.dash.set_goal("18:00", p.clock_s)
        self.per_hour[0] = 14.0
        self.true[0] = 90.0
        s = self.poll(p, 3600)
        self.assertEqual(s["battery"]["drain"]["basis"], "measured")
        self.assertAlmostEqual(s["battery"]["drain"]["per_hour"], 14.0, delta=0.8)
        self.assertLess(s["battery"]["hours_left"], 6.0)
        self.assertFalse(s["battery"]["goal_met"])
        self.assertIn("Won't last until 6 PM", s["battery"]["goal_note"])

    def test_visible_helper_reads_the_same_numbers(self):
        p = self.phone(visible=True)
        s = p.state()
        self.assertTrue(s["battery"]["why"].endswith("; drain from the model, not measured yet (0 of 10 min)"))
        s = self.poll(p, 3600)
        self.assertRegex(s["battery"]["why"], r"; drain measured at 5\.\d%/h over \d+ min$")
        h = s["helper"]
        self.assertEqual(h["time_left"], s["battery"]["time_left"])
        self.assertAlmostEqual(h["hours_left"], s["battery"]["hours_left"], delta=0.6)

    def test_hidden_helper_still_carries_the_labelled_rate(self):
        s = self.phone().state()
        self.assertNotIn("why", s["battery"])
        self.assertEqual(s["battery"]["drain"]["basis"], "model")

    def test_level_steps_no_longer_spike_the_reserve(self):
        p = self.phone(visible=True)
        self.true[0] = 70.6
        self.per_hour[0] = 12.0
        raw, peak, raw_peak = DrainEstimator(), 0.0, 0.0
        for _ in range(240):
            s = self.poll(p, 15)
            peak = max(peak, s["helper"]["reserve"])
            raw.add(Sample(p.sim.t_s, p.sim.level))
            raw_peak = max(raw_peak, essentials_reserve(raw.rate()))
        self.assertGreater(raw_peak, 30.0)  # the raw level-step estimator reads each 1 point step as a burst of drain
        self.assertLess(peak, 15.0)
        self.assertEqual(s["battery"]["drain"]["basis"], "measured")

    def test_no_helper_hours_left_before_there_is_a_measurement(self):
        p = self.phone(visible=True)
        s = self.poll(p, 120)
        self.assertEqual(s["battery"]["drain"]["basis"], "model")
        self.assertIsNone(s["helper"]["hours_left"])
        self.assertEqual(s["helper"]["reserve"], 0.0)

    def test_charging_shows_charging_and_a_model_basis(self):
        self.charging[0] = True
        p = self.phone(visible=True)
        s = p.state()
        self.assertEqual(s["battery"]["time_left"], "Charging")
        self.assertEqual(s["battery"]["drain"]["basis"], "model")
        self.assertIsNone(s["battery"]["drain"]["measured_per_hour"])
        self.assertNotIn("drain", s["battery"]["why"])

    def test_tick_in_live_mode_still_does_not_simulate(self):
        p = self.phone()
        before = p.sim.level
        p.tick(1)
        self.assertEqual(p.sim.level, before)

    def test_simulated_phone_is_unchanged(self):
        p = Phone()
        self.assertIsInstance(p.runtime.estimator, DrainEstimator)
        s = p.state()
        self.assertNotIn("drain", s["battery"])
        self.assertNotIn("battery_source", s)
        self.assertEqual(s["battery"]["time_left"], time_left_words(s["battery"]["hours_left"]))
        self.assertAlmostEqual(s["battery"]["hours_left"], s["battery"]["percent"] / p._tick_obj.drain_per_h, places=1)
        p.set_sim(True)
        p.tick(1)
        self.assertNotIn("drain", p.state()["battery"])


if __name__ == "__main__":
    unittest.main()
