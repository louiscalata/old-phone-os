# SPDX-License-Identifier: GPL-3.0-or-later
"""Behaviour the hinge, posture and back-mode tests left open, found by mutation testing (facing.py, posture.py, the
pure parts of hinge.py, the back-mode functions of layout.py and Blanker._blank_some). Each test pins one rule the
modules' docstrings state: a border value, a reading that says nothing, a retry that waits, a panel left as it is."""

import os
import threading
import time
import unittest

from opos.native import blank, facing, hinge, layout, posture
from tests.test_native_blank import Tree
from tests.test_native_hinge import AccelBus, FakeBus

POSIX = hasattr(os, "getuid")


class FacingBorders(unittest.TestCase):
    def test_exactly_a_third_of_a_g_and_a_bit_is_not_yet_a_verdict(self):
        # "z above 0.35 g means the right screen ... z below -0.35 g means the left one": the border itself says nothing
        self.assertIsNone(facing.verdict(0.35 * 1024))
        self.assertIsNone(facing.verdict(-0.35 * 1024))
        self.assertEqual(facing.verdict(0.35 * 1024 + 0.01), "right")
        self.assertEqual(facing.verdict(-0.35 * 1024 - 0.01), "left")


class PostureBorders(unittest.TestCase):
    def test_classify_says_nothing_for_a_bool(self):
        for angle in (True, False):
            for previous in (None, "closed"):
                self.assertIsNone(posture.classify(angle, previous), (angle, previous))

    def test_a_reading_outside_the_range_says_nothing_even_next_to_the_held_zone(self):
        for angle, previous in ((-1, "closed"), (-0.5, "closed"), (361, "back"), (360.5, "back"), (362, "back")):
            with self.subTest(angle=angle, previous=previous):
                self.assertIsNone(posture.classify(angle, previous))
        self.assertEqual(posture.posture(False, 362, "back"), "open", "a reading past 360 leaves it open, not back")
        self.assertEqual(posture.classify(360, "back"), "back")
        self.assertEqual(posture.classify(0, "closed"), "closed")

    def test_a_posture_is_held_exactly_hyst_degrees_below_its_zone(self):
        self.assertEqual(posture.posture(False, 164, "flat"), "flat")  # 170 - 6
        self.assertEqual(posture.posture(False, 196, "flat"), "tent")  # 190 + 6 is already outside
        self.assertEqual(posture.posture(False, 339, "back"), "back")  # 345 - 6

    def test_the_settings_line_has_no_reading_past_360(self):
        self.assertTrue(posture.describe("back", 360.5).startswith("No hinge reading"))
        self.assertTrue(posture.describe("back", 360).startswith("Back, hinge at 360"))


class ScreenBorders(unittest.TestCase):
    def test_a_screen_with_no_width_is_no_screen_but_half_a_pixel_is_one(self):
        for x in (0, 21, 42):
            self.assertIsNone(layout.screen_at(x, 42, 42), x)  # the hinge fills the window
        self.assertEqual(layout.screen_at(0, 43, 42), 0)  # each screen is half a pixel wide
        self.assertEqual(layout.screen_at(43, 43, 42), 1)

    def test_touch_ignored_answers_true_or_false(self):
        W, GAP = 1392, 42
        self.assertIs(layout.touch_ignored((100, 300), W, GAP, None), False)
        self.assertIs(layout.touch_ignored((100, 300), W, GAP, 0), False)
        self.assertIs(layout.touch_ignored((1000, 300), W, GAP, 0), True, "on the dark right screen")
        self.assertIs(layout.touch_ignored((690, 300), W, GAP, 0), True, "in the gap")

    def test_in_back_mode_the_edge_band_is_exactly_edge_pixels_on_each_side_of_the_screen_in_use(self):
        W, H, HG = 1392, 1800, 42
        pane = (W - HG) / 2  # 675
        swipe = lambda x, active: layout.swipe_action("app", False, (x, 400), W, H, 500, 0, HG, active)
        cases = {
            0: ((0, "home"), (47, "home"), (48, None), (300, None), (627, None), (628, "home"), (674, "home")),
            1: (
                (pane + HG, "home"),
                (pane + HG + 47, "home"),
                (pane + HG + 48, None),
                (1000, None),
                (W - 48, None),
                (W - 47, "home"),
            ),
        }
        for active, xs in cases.items():
            for x, want in xs:
                with self.subTest(active=active, x=x):
                    self.assertEqual(swipe(x, active), want)


class WatcherClock(unittest.TestCase):
    """The watcher's waits are measured on its own clock: a clock that does not move must not open a retry, and must
    not stop a first open or a report."""

    def run_watcher(self, bus, **kw):
        angles, tilts, lock = [], [], threading.Lock()

        def on_angle(a):
            with lock:
                angles.append(a)

        def on_tilt(z):
            with lock:
                tilts.append(z)

        kw.setdefault("tilt_min_s", 0)
        w = hinge.HingeWatcher(
            on_angle, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0, on_tilt=on_tilt, **kw
        ).start()
        bus.done.wait(5)
        w.stop()
        with lock:
            return list(angles), list(tilts)

    def test_a_clock_at_zero_still_opens_the_accelerometer_at_once_and_reports_each_move(self):
        bus = AccelBus([150] * 8, [100, 200, 300])
        _angles, tilts = self.run_watcher(bus, clock=lambda: 0.0)
        self.assertEqual(tilts[:3], [100, 200, 300])

    def test_a_failed_accelerometer_is_not_tried_again_before_retry_s_has_passed(self):
        bus = AccelBus([150] * 60, [1], accel_error=RuntimeError("no plugin"))
        _angles, tilts = self.run_watcher(bus, clock=lambda: 0.0)
        self.assertEqual(bus.accel_opened, 1, "the clock never moved, so it was never time to try again")
        self.assertEqual(tilts, [None])

    def test_a_hinge_found_again_opens_the_accelerometer_again(self):
        bus = AccelBus([120, RuntimeError("gone")] + [130] * 5, [800])
        angles, tilts = self.run_watcher(bus, clock=lambda: 0.5)
        self.assertEqual(angles[:3], [120, None, 130])
        self.assertGreaterEqual(bus.accel_opened, 2)
        self.assertEqual(tilts[:3], [800, None, 800], "the accelerometer is said again after the hinge came back")

    def test_slow_mode_still_reads_the_accelerometer_when_it_is_due(self):
        bus = AccelBus([170] * 30, [600])
        _angles, tilts = self.run_watcher(bus, clock=lambda: 0.0, calm_s=0, slow_poll_s=0.001, slow_tilt_s=0)
        self.assertEqual(tilts[:1], [600])

    def test_a_move_of_exactly_tilt_step_is_reported_and_one_less_is_not(self):
        bus = AccelBus([150] * 10, [1000, 1007, 1008, 1015, 1016])
        _angles, tilts = self.run_watcher(bus)
        self.assertEqual(tilts[:3], [1000, 1008, 1016])

    def test_without_on_tilt_a_lost_hinge_says_nothing_about_the_accelerometer(self):
        said = []
        old = hinge._say
        hinge._say = said.append
        try:
            bus = FakeBus([120, RuntimeError("gone")])
            w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0).start()
            bus.done.wait(5)
            time.sleep(0.05)
            w.stop()
        finally:
            hinge._say = old
        self.assertEqual([s for s in said if "tilt" in s], [], said)

    def test_the_thread_never_keeps_the_shell_from_exiting(self):
        bus = FakeBus([120] * 3)
        w = hinge.HingeWatcher(lambda a: None, make_bus=lambda: bus, poll_s=0.001, retry_s=0.02, settle_s=0).start()
        try:
            self.assertTrue(w.thread.daemon)
        finally:
            w.stop()


class StillBus(FakeBus):
    """A hinge that stays at one angle; counts the angle and tilt readings."""

    def __init__(self, angle):
        super().__init__([angle])
        self.value, self.asked, self.tilts_read = angle, 0, 0
        self.accel_opened = self.accel_closed = 0

    def angle(self):
        self.asked += 1
        return self.value

    def tilt(self):
        self.tilts_read += 1
        return 500

    def open_accel(self):
        self.accel_opened += 1

    def close_accel(self):
        self.accel_closed += 1


class WatcherRate(unittest.TestCase):
    def watch(self, bus, **kw):
        kw.setdefault("poll_s", 0.01)
        return hinge.HingeWatcher(
            lambda a: None, make_bus=lambda: bus, on_tilt=lambda z: None, settle_s=0, retry_s=0.05, **kw
        ).start()

    def asked_in(self, bus, seconds, **kw):
        w = self.watch(bus, **kw)
        time.sleep(seconds)
        w.stop()
        return bus.asked

    def test_300_degrees_is_already_fast_and_299_is_not(self):
        self.assertGreater(self.asked_in(StillBus(300), 0.35, slow_poll_s=0.5, slow_tilt_s=0.5, calm_s=0.05), 12)
        self.assertLess(self.asked_in(StillBus(299), 0.35, slow_poll_s=0.5, slow_tilt_s=0.5, calm_s=0.05), 10)

    def test_a_calm_time_of_zero_means_never_moving(self):
        self.assertLess(self.asked_in(StillBus(170), 0.3, slow_poll_s=0.5, calm_s=0, clock=lambda: 0.0), 4)

    def test_waking_from_a_pause_reads_fast_for_a_moment(self):
        bus = StillBus(170)
        w = self.watch(bus, slow_poll_s=0.2, slow_tilt_s=1.0, calm_s=0.1)
        time.sleep(0.3)  # calm: slow now
        w.set_paused(True)
        time.sleep(0.4)  # it notices within one slow poll and rests
        w.set_paused(False)
        before = bus.asked
        time.sleep(0.08)
        after = bus.asked
        w.stop()
        self.assertGreater(after - before, 3, "fast reads right after waking")

    def test_paused_the_thread_sleeps_instead_of_spinning(self):
        bus = StillBus(170)
        w = self.watch(bus)
        time.sleep(0.1)
        w.set_paused(True)
        time.sleep(0.05)
        cpu = time.process_time()
        time.sleep(0.3)
        used = time.process_time() - cpu
        w.set_paused(False)
        w.stop()
        self.assertLess(used, 0.1, "a paused watcher costs no CPU")

    def test_resumed_with_a_clock_that_did_not_move_the_accelerometer_opens_at_once(self):
        bus = StillBus(170)
        w = self.watch(bus, clock=lambda: 0.0)
        time.sleep(0.1)
        w.set_paused(True)
        time.sleep(0.1)
        w.set_paused(False)
        time.sleep(0.15)
        w.stop()
        self.assertGreaterEqual(bus.accel_closed, 1)
        self.assertGreaterEqual(bus.accel_opened, 2)


class SensorBusTilt(unittest.TestCase):
    """SensorBus.tilt: a started sensor reads all zeros until its first report; any non-zero field is a reading."""

    def tilt(self, xyz):
        bus = hinge.SensorBus.__new__(hinge.SensorBus)
        bus.accel_sock = None
        bus._call = lambda *a, **k: (xyz,)
        return bus.tilt()

    def test_all_zeros_is_no_report_yet(self):
        self.assertIsNone(self.tilt((0, 0, 0, 0)))

    def test_any_field_that_is_not_zero_makes_it_a_reading(self):
        self.assertEqual(self.tilt((7, 0, 0, 0)), 0)
        self.assertEqual(self.tilt((0, 3, 0, 0)), 0)
        self.assertEqual(self.tilt((0, 0, 3, 0)), 0)
        self.assertEqual(self.tilt((0, 0, 0, -1000)), -1000)
        self.assertEqual(self.tilt((9, 1, 2, 512)), 512)


@unittest.skipUnless(POSIX, "needs a private runtime folder (Linux and the phone have one)")
class OnePanelEdges(unittest.TestCase):
    def test_refusals_answer_false(self):
        t = Tree(self)
        for bad in ((0, 1), (), (7,), "x"):
            self.assertIs(t.blanker.blank(only=bad), False, bad)
        t.run.chmod(0o777)
        self.assertIs(t.blanker.blank(only=(1,)), False)
        self.assertEqual(t.values(), (90, 200))

    def test_a_save_that_fails_darkens_nothing_and_answers_false(self):
        t = Tree(self)

        def fails(values):
            raise OSError(28, "No space left on device")

        t.blanker._save = fails
        self.assertIs(t.blanker.blank(only=(1,)), False)
        self.assertEqual((t.values(), t.saved.exists()), ((90, 200), False))
        self.assertTrue(any("No space left on device" in s for s in t.said), t.said)

    def test_a_link_where_the_saved_file_goes_is_never_darkened_on(self):
        t = Tree(self)
        target = t.root / "elsewhere"
        target.write_text("250 250\n", encoding="ascii")
        t.saved.symlink_to(target)
        self.assertIs(t.blanker.blank(only=(1,)), False)
        self.assertEqual(t.values(), (90, 200))
        self.assertEqual(target.read_text(encoding="ascii"), "250 250\n")

    def test_a_folder_where_the_saved_file_goes_is_never_darkened_on(self):
        t = Tree(self)
        t.saved.mkdir()
        self.assertIs(t.blanker.blank(only=(1,)), False)
        self.assertNotIn(0, t.values())

    def test_the_values_read_now_are_saved_not_the_defaults(self):
        t = Tree(self, (50, 120))
        self.assertTrue(t.blanker.blank(only=(1,)))
        self.assertEqual(t.saved.read_text().split(), ["50", "120"])
        self.assertEqual(t.values(), (50, 0))

    def test_a_dark_panel_is_saved_as_its_default(self):
        t = Tree(self, (0, 120))
        t.blanker.blank(only=(1,))
        self.assertEqual(t.saved.read_text().split(), [str(blank.DEFAULTS[0]), "120"])

    def test_a_lit_panel_is_left_at_the_brightness_it_has_now(self):
        t = Tree(self)
        t.blanker.blank(only=(1,))
        t.brightness("panel0-backlight").write_text("150\n", encoding="ascii")  # turned up since
        t.blanker.blank(only=(1,))
        self.assertEqual(t.values(), (150, 0), "only a panel that reads 0 is lit from the saved file")


if __name__ == "__main__":
    unittest.main()
