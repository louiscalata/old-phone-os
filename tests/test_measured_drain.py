# SPDX-License-Identifier: GPL-3.0-or-later
"""Measured drain: the discharge rate a live battery shows in its own readings."""
import json
import math
import random
import tempfile
import unittest
from pathlib import Path

from opos.hw import live_battery as lb
from opos.hw.live_battery import LiveBattery, MeasuredDrain


class Trace:
    """A synthetic discharge: the true level falls at a set rate and the gauge shows whole percent."""

    def __init__(self, md=None, level=80.0, t=0.0, cap=3_000_000):
        self.md, self.true, self.t, self.cap = md or MeasuredDrain(), level, t, cap

    def feed(self, seconds, per_hour, step=15.0, charging=False, jitter=0.0, rng=None, counter=False, quant=round):
        end = self.t + seconds
        while self.t < end - 1e-9:
            self.t += step
            self.true -= per_hour * step / 3600
            shown = quant(self.true + (rng.uniform(-jitter, jitter) if rng else 0))
            c = round(self.true / 100 * self.cap) if counter else None
            self.md.observe(self.t, float(min(100, max(0, shown))), charging, c)
        return self.md.rate(self.t)

    def skip(self, seconds, lost):
        self.t += seconds
        self.true -= lost


class MeasuredDrainTests(unittest.TestCase):
    def assertNear(self, got, want, pct):
        self.assertIsNotNone(got)
        self.assertLessEqual(abs(got - want), want * pct / 100, f"{got} is not within {pct}% of {want}")

    def test_linear_discharge_is_measured_whatever_the_speed(self):
        for per_hour, pct in ((3, 8), (12, 5), (40, 8)):
            with self.subTest(per_hour=per_hour):
                tr = Trace()
                self.assertIsNone(tr.feed(300, per_hour))  # five minutes in: too soon to say
                self.assertNear(tr.feed(5400, per_hour), per_hour, pct)

    def test_not_trusted_before_the_window_and_the_drop(self):
        tr = Trace()
        for _ in range(40):  # 10 minutes of readings, but the window counts from the first level step
            self.assertIsNone(tr.feed(15, 12))
        self.assertLess(tr.md.window_s, 600)
        self.assertIsNotNone(tr.feed(900, 12))
        self.assertGreaterEqual(tr.md.window_s, 600)

    def test_quantisation_does_not_read_as_a_whole_point_per_reading(self):
        # One point seen between two readings 15 s apart would be 240 %/h if taken at face value.
        for quant in (round, math.floor, math.ceil):
            with self.subTest(quant=quant.__name__):
                tr = Trace(level=80.4)
                self.assertNear(tr.feed(3600, 12, quant=quant), 12, 5)

    def test_noisy_gauge_that_bounces_stays_close(self):
        tr = Trace()
        got = tr.feed(5400, 12, jitter=0.45, rng=random.Random(7))
        self.assertNear(got, 12, 15)

    def test_a_point_of_bounce_is_not_a_reset(self):
        md = MeasuredDrain()
        for i, level in enumerate((80, 80, 79, 80, 79, 79, 80, 79, 78, 79, 78)):
            md.observe(15.0 * (i + 1), float(level), False)
        self.assertGreater(md.window_s, 0)
        self.assertEqual(md.t_last, 165.0)

    def test_big_rise_or_drop_starts_the_run_again(self):
        for jump, label in ((+4, "rise"), (-6, "drop")):
            with self.subTest(label):
                tr = Trace()
                self.assertIsNotNone(tr.feed(3600, 12))
                tr.skip(15, 0)
                tr.true = round(tr.true) + jump
                tr.md.observe(tr.t, float(round(tr.true)), False)
                self.assertIsNone(tr.md.rate(tr.t))
                self.assertEqual(tr.md.window_s, 0.0)

    def test_stalled_level_pulls_the_rate_down_instead_of_holding_it(self):
        tr = Trace()
        self.assertNear(tr.feed(3600, 12), 12, 5)
        hour = tr.feed(3600, 0)
        self.assertLessEqual(hour, 1.2)  # under one more point in the hour: at most about 1 %/h
        three = tr.feed(2 * 3600, 0)
        self.assertLessEqual(three, 0.5)
        self.assertGreaterEqual(three, lb.DRAIN_MIN)
        self.assertNear(tr.feed(3 * 3600, 12), 12, 10)  # and it climbs back once the drain does

    def test_plugging_in_and_unplugging_start_over(self):
        tr = Trace()
        self.assertNear(tr.feed(3600, 12), 12, 5)
        self.assertIsNone(tr.feed(60, -20, charging=True))
        self.assertEqual(tr.md.window_s, 0.0)
        self.assertIsNone(tr.feed(60, 24))  # unplugged: nothing carried over, nothing trusted yet
        got = tr.feed(3600, 24)
        self.assertNear(got, 24, 6)  # not blended with the 12 %/h before the plug

    def test_wake_from_a_long_gap_does_not_pollute_the_rate(self):
        tr = Trace()
        self.assertNear(tr.feed(3600, 12), 12, 5)
        tr.skip(3 * 3600, 20)  # asleep for three hours: 20 points at about 6.7 %/h
        self.assertIsNone(tr.feed(15, 12))
        self.assertEqual(tr.md.window_s, 0.0)
        self.assertNear(tr.feed(2400, 12), 12, 8)

    def test_a_short_gap_keeps_the_run(self):
        tr = Trace()
        self.assertNear(tr.feed(3600, 12), 12, 5)
        tr.skip(300, 1)  # nobody read the battery for five minutes; it lost a point (12 %/h)
        self.assertNear(tr.feed(1800, 12), 12, 8)

    def test_a_gap_cuts_only_the_segment_it_falls_in(self):
        tr = Trace()
        self.assertNear(tr.feed(3600, 12), 12, 5)
        tr.skip(600, 0.3)  # ten minutes unread, the phone asleep and nearly idle: its steps cannot be timed
        self.assertNear(tr.feed(15, 12), 12, 5)
        self.assertNear(tr.feed(1800, 12), 12, 6)  # the average kept the 12 %/h; the idle stretch never entered it

    def test_stall_after_a_gap_is_still_capped(self):
        tr = Trace()
        self.assertNear(tr.feed(3600, 12), 12, 5)
        tr.skip(300, 0)
        self.assertLessEqual(tr.feed(3 * 3600, 0), 0.5)

    def test_a_gap_of_a_quarter_hour_starts_over_even_when_the_level_barely_moved(self):
        tr = Trace()
        self.assertNear(tr.feed(3600, 12), 12, 5)
        tr.skip(2 * 3600, 3)  # an idle two hours on three points: not the drain it was measuring
        self.assertIsNone(tr.feed(15, 12))
        self.assertEqual(tr.md.window_s, 0.0)

    def test_the_average_follows_a_change_in_drain_gradually(self):
        tr = Trace(level=99.9)
        self.assertNear(tr.feed(1800, 12), 12, 5)
        quarter = tr.feed(900, 24)
        self.assertGreater(quarter, 14)
        self.assertLess(quarter, 22)  # about 30 minutes to forget the old rate, not an instant switch
        self.assertNear(tr.feed(2 * 3600, 24), 24, 5)

    def test_a_step_is_timed_midway_between_the_readings_that_bracket_it(self):
        md = MeasuredDrain()
        readings = [(0, 80)] + [(t, 79) for t in range(100, 601, 100)] + [(620, 78)]
        for t, level in readings:  # the first step is seen at 100 (so it is stamped 50), the second at 620 (stamped 610)
            md.observe(float(t), float(level), False)
        self.assertAlmostEqual(md.ema, 3600 / 560, places=6)

    def test_stale_readings_stop_being_trusted(self):
        tr = Trace()
        self.assertIsNotNone(tr.feed(3600, 12))
        self.assertIsNotNone(tr.md.rate(tr.t + lb.DRAIN_MAX_GAP_S))
        self.assertIsNone(tr.md.rate(tr.t + lb.DRAIN_MAX_GAP_S + 1))

    def test_implausibly_fast_segments_are_never_trusted(self):
        tr = Trace(level=90.0)
        self.assertIsNone(tr.feed(240, 600))
        self.assertIsNone(tr.md.rate(tr.t))

    def test_repeated_or_backwards_clock_is_ignored(self):
        tr = Trace()
        tr.feed(1200, 12)
        before = (tr.md.t_last, tr.md.window_s, tr.md.ema)
        tr.md.observe(tr.t, 1.0, False)
        tr.md.observe(tr.t - 100, 99.0, False)
        self.assertEqual((tr.md.t_last, tr.md.window_s, tr.md.ema), before)

    def test_charge_counter_gives_an_estimate_sooner(self):
        # 3 %/h is a level step only every 20 minutes; the counter sees 0.25 point every five.
        by_level, by_counter = Trace(), Trace()
        self.assertIsNone(by_level.feed(720, 3))
        got = by_counter.feed(720, 3, counter=True)
        self.assertEqual(by_counter.md.via, "counter")
        self.assertNear(got, 3, 5)
        self.assertEqual(by_level.md.via, "level")

    def test_counter_noise_and_quantisation_together(self):
        rng = random.Random(3)
        tr = Trace()
        for _ in range(240):
            tr.t += 15
            tr.true -= 8 * 15 / 3600
            c = round(tr.true / 100 * tr.cap + rng.uniform(-1500, 1500))  # +-0.05 point of counter noise
            tr.md.observe(tr.t, float(round(tr.true)), False, c)
        self.assertEqual(tr.md.via, "counter")
        self.assertNear(tr.md.rate(tr.t), 8, 10)

    def test_counter_that_disagrees_with_the_level_is_dropped_for_the_run(self):
        tr = Trace()
        for _ in range(480):  # the counter freezes while the level keeps falling
            tr.t += 15
            tr.true -= 12 * 15 / 3600
            tr.md.observe(tr.t, float(round(tr.true)), False, 2_400_000)
        self.assertEqual(tr.md.via, "level")
        self.assertNear(tr.md.rate(tr.t), 12, 12)

    def test_counter_is_only_calibrated_from_a_high_enough_level(self):
        tr = Trace(level=25.0)
        tr.feed(1800, 12, counter=True)
        self.assertEqual(tr.md.via, "level")

    def test_charging_readings_measure_nothing(self):
        md = MeasuredDrain()
        for i in range(100):
            md.observe(15.0 * (i + 1), 50.0, True)
        self.assertIsNone(md.rate(1500.0))
        self.assertEqual(md.window_s, 0.0)
        self.assertIsNone(md.via)


class LiveBatteryDrainTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.level = [80.0]
        self.charging = [False]
        self.counter = [None]
        self.fail = [False]

    def reader(self):
        if self.fail[0]:
            return None
        r = {"level": round(self.level[0]), "temp_c": 29.0, "charging": self.charging[0], "source": "sysfs",
             "health": 0.7, "health_source": "OPOS_BATTERY_HEALTH"}
        if self.counter[0] is not None:
            r["charge_counter_uah"] = self.counter[0]
        return r

    def battery(self):
        return LiveBattery(reader=self.reader, clock=lambda: self.now[0], refresh_s=15)

    def run_for(self, b, seconds, per_hour):
        end = self.now[0] + seconds
        while self.now[0] < end - 1e-9:
            self.now[0] += 15
            self.level[0] -= per_hour * 15 / 3600
            b.refresh()

    def test_model_until_trusted_then_measured(self):
        b = self.battery()
        use, info = b.drain_rate(23.0)
        self.assertEqual(use, 23.0)
        self.assertEqual((info["basis"], info["per_hour"], info["measured_per_hour"], info["model_per_hour"]), ("model", 23.0, None, 23.0))
        self.assertEqual((info["age_s"], info["window_s"], info["via"]), (0.0, 0.0, "level"))
        self.run_for(b, 300, 6)
        self.assertEqual(b.drain_rate(23.0)[1]["basis"], "model")
        self.run_for(b, 3000, 6)
        use, info = b.drain_rate(23.0)
        self.assertEqual(info["basis"], "measured")
        self.assertAlmostEqual(use, 6.0, delta=0.4)
        self.assertEqual(info["per_hour"], round(use, 2))
        self.assertEqual(info["model_per_hour"], 23.0)
        self.assertEqual(info["measured_per_hour"], info["per_hour"])
        self.assertGreaterEqual(info["window_s"], 600)
        json.dumps(info)

    def test_age_grows_while_the_battery_cannot_be_read_and_the_estimate_lapses(self):
        b = self.battery()
        self.run_for(b, 3600, 6)
        self.assertEqual(b.drain_rate(23.0)[1]["basis"], "measured")
        self.fail[0] = True
        self.now[0] += 60
        b.refresh()
        info = b.drain_rate(23.0)[1]
        self.assertEqual((info["basis"], info["age_s"]), ("measured", 60.0))
        self.now[0] += lb.DRAIN_MAX_GAP_S
        info = b.drain_rate(23.0)[1]
        self.assertEqual(info["basis"], "model")
        self.assertGreater(info["age_s"], lb.DRAIN_MAX_GAP_S)
        self.fail[0] = False
        self.level[0] -= 10
        self.now[0] += 15
        b.refresh()
        info = b.drain_rate(23.0)[1]  # a reading after the outage starts a new run
        self.assertEqual((info["basis"], info["window_s"]), ("model", 0.0))

    def test_charging_is_model_basis_with_nothing_measured(self):
        b = self.battery()
        self.run_for(b, 3600, 6)
        self.charging[0] = True
        self.run_for(b, 30, 0)
        info = b.drain_rate(23.0)[1]
        self.assertEqual((info["basis"], info["measured_per_hour"], info["window_s"]), ("model", None, 0.0))

    def test_counter_from_the_reader_is_used(self):
        b = self.battery()
        for _ in range(48):
            self.now[0] += 15
            self.level[0] -= 3 * 15 / 3600
            self.counter[0] = round(self.level[0] / 100 * 3_000_000)
            b.refresh()
        info = b.drain_rate(23.0)[1]
        self.assertEqual((info["basis"], info["via"]), ("measured", "counter"))
        self.assertAlmostEqual(info["per_hour"], 3.0, delta=0.2)

    def test_estimator_stands_in_for_the_runtime_one(self):
        b = self.battery()
        est = b.rate_estimator()
        est.add(object())
        self.assertIsNone(est.rate())
        self.run_for(b, 3600, 6)
        self.assertAlmostEqual(est.rate(), 6.0, delta=0.4)

    def test_words_name_the_basis(self):
        b = self.battery()
        self.assertEqual(b.drain_words(b.drain_rate(23.0)[1]), "drain from the model, not measured yet (0 of 10 min)")
        self.run_for(b, 3600, 6)
        words = b.drain_words(b.drain_rate(23.0)[1])
        self.assertRegex(words, r"^drain measured at [56]\.\d%/h over \d+ min$")


class SysfsCounterTests(unittest.TestCase):
    def _root(self, supplies):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, files in supplies.items():
            base = Path(d.name) / "sys/class/power_supply" / name
            base.mkdir(parents=True)
            for k, v in files.items():
                (base / k).write_text(v)
        return d.name

    def test_charge_counter_then_charge_now_then_energy_now(self):
        for files, want in (({"charge_counter": "2400000", "charge_now": "1"}, 2400000), ({"charge_now": "1800000"}, 1800000), ({"energy_now": "9000000"}, 9000000)):
            with self.subTest(files=files):
                root = self._root({"battery": dict({"type": "Battery", "status": "Discharging", "capacity": "60"}, **files)})
                self.assertEqual(lb._sysfs_counter(root), want)
                self.assertEqual(lb._sysfs_status(root)["counter"], want)

    def test_none_when_absent_ambiguous_or_not_a_battery(self):
        self.assertIsNone(lb._sysfs_counter(self._root({"battery": {"type": "Battery", "capacity": "60"}})))
        two = {"bq-0": {"type": "Battery", "charge_now": "1000"}, "bq-1": {"type": "Battery", "charge_now": "1100"}}
        self.assertIsNone(lb._sysfs_counter(self._root(two)))
        self.assertIsNone(lb._sysfs_counter(self._root({"usb": {"type": "USB", "charge_now": "1000"}})))
        self.assertIsNone(lb._sysfs_counter("/nonexistent"))

    def test_read_live_hands_the_counter_over_without_touching_health(self):
        root = self._root({"battery": {"type": "Battery", "status": "Discharging", "capacity": "60", "charge_now": "1800000"}})
        r = lb.read_live(env={}, root=root, which=lambda name: None)
        self.assertEqual((r["counter"], r["health"], r["health_source"]), (1800000, 1.0, "assumed"))


if __name__ == "__main__":
    unittest.main()
