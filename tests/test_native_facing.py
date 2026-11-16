# SPDX-License-Identifier: GPL-3.0-or-later
"""facing.py: which screen faces the user while folded back, from the accelerometer z reading."""

import unittest

from opos.native import facing


class Verdict(unittest.TestCase):
    def test_the_measured_readings(self):
        for z, want in (
            (920, "right"),
            (945, "right"),
            (1024, "right"),
            (-945, "left"),
            (-800, "left"),
            (-1015, "left"),
            (0, None),
            (200, None),
            (-200, None),
            (358, None),
            (359, "right"),
            (-358, None),
            (-359, "left"),
        ):
            with self.subTest(z=z):
                self.assertEqual(facing.verdict(z), want)

    def test_nothing_that_is_not_a_number_says_anything(self):
        for z in (None, "x", True, float("nan"), float("inf"), -float("inf")):
            self.assertIsNone(facing.verdict(z), z)


class Holding(unittest.TestCase):
    def test_the_fallback_stays_until_a_verdict_has_held(self):
        f = facing.Facing(fallback="left")
        self.assertEqual(f.update(900, 0.0), "left", "one reading is not enough")
        self.assertEqual(f.update(900, 0.5), "left")
        self.assertEqual(f.update(900, 0.8), "right", "held for HOLD_S")
        self.assertEqual(f.screen(), "right")

    def test_a_flip_needs_to_hold_too(self):
        f = facing.Facing(fallback="left")
        for t in (0.0, 0.5, 0.9):
            f.update(900, t)
        self.assertEqual(f.current, "right")
        self.assertEqual(f.update(-900, 1.0), "right")
        self.assertEqual(f.update(-900, 1.5), "right")
        self.assertEqual(f.update(-900, 1.9), "left")

    def test_a_wobble_never_flips_it(self):
        f = facing.Facing(fallback="left")
        for t in (0.0, 0.5, 0.9):
            f.update(900, t)
        for i in range(40):  # alternating readings every 0.2 s: no verdict holds for 0.8 s
            f.update(-900 if i % 2 else 900, 1.0 + 0.2 * i)
        self.assertEqual(f.current, "right")

    def test_an_unclear_reading_keeps_the_verdict_but_breaks_a_run(self):
        f = facing.Facing(fallback="left")
        for t in (0.0, 0.5, 0.9):
            f.update(900, t)
        f.update(-900, 1.0)
        f.update(0, 1.4)  # held upright: unclear, the run towards "left" is broken
        self.assertEqual(f.update(-900, 1.9), "right")
        self.assertEqual(f.update(-900, 2.8), "left")

    def test_reset_goes_back_to_the_fallback(self):
        f = facing.Facing(fallback="left")
        for t in (0.0, 0.5, 0.9):
            f.update(900, t)
        f.reset()
        self.assertEqual(f.screen(), "left")
        f.fallback = "right"
        self.assertEqual(f.screen(), "right")

    def test_without_a_fallback_and_without_a_verdict_nothing_is_chosen(self):
        self.assertIsNone(facing.Facing().update(0, 0.0))


if __name__ == "__main__":
    unittest.main()
