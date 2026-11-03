# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from opos.web import Phone
from tests.web_source import web_text


class DemoPanelTest(unittest.TestCase):
    def test_toggle_sim_sends_demo_speed(self):
        text = web_text()
        marker = "async function toggleSim"
        idx = text.find(marker)
        line = text[idx:text.find("\n", idx)]
        self.assertNotEqual(idx, -1)
        self.assertIn("speed:60", line)
        self.assertIn("running:!running", line)
        phone = Phone()
        phone.set_sim(True, 60)
        self.assertEqual(phone.sim.speed, 60)
        self.assertTrue(phone.running)
