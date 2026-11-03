# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from opos.web import page


class TestMeterInitialAccessibility(unittest.TestCase):
    def test_meter_initial_aria_label(self):
        html = page()
        self.assertIn('<button type="button" id="meter" data-tone="normal" aria-label="Battery, pull down for details"', html)
