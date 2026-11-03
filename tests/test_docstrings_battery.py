# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import opos.battery as m


class TestBatteryDocstrings(unittest.TestCase):
    def test_docstrings(self):
        for obj in (m, m.DrainEstimator, m.hours_left, m.essentials_reserve):
            self.assertTrue((obj.__doc__ or "").strip())
