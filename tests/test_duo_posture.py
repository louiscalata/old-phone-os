# SPDX-License-Identifier: GPL-3.0-or-later
"""Hinge angles map to a posture and do not flap inside 10 degrees."""
import unittest

from opos.posture import enabled, replay


class PostureReplay(unittest.TestCase):
    def test_each_band_has_a_name(self):
        samples = (0, 45, 100, 170, 250, 330)
        names = replay(samples)
        self.assertEqual(names, ["closed", "peek", "book", "flat", "tent", "folded-back"])

    def test_jitter_inside_ten_degrees_does_not_flap(self):
        # Book is 80-130. 100, then 92, 108, 88, 112 stay book. 69 is more than 10 outside 80.
        names = replay([100, 92, 108, 88, 112, 69])
        self.assertEqual(names[:-1], ["book"] * 5)
        self.assertEqual(names[-1], "peek")

    def test_the_flag_is_off_unless_set(self):
        self.assertFalse(enabled({}))
        self.assertFalse(enabled({"OPOS_POSTURE": "0"}))
        self.assertTrue(enabled({"OPOS_POSTURE": "1"}))


if __name__ == "__main__":
    unittest.main()
