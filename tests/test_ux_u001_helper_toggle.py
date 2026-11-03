# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.home import is_quiet
from opos.web import page


class HelperToggleTest(unittest.TestCase):
    def test_helper_toggle_outside_demo(self):
        h = page()
        self.assertIn('<button data-go="receive">Simulate incoming text</button><button id="helpertoggle">Show helper</button></div>', h)
        self.assertLess(h.index('<button id="helpertoggle">Show helper</button>'), h.index('<div id="demo"'))
        self.assertIn("document.getElementById('helpertoggle').onclick=toggleVis;", h)
        self.assertTrue(is_quiet(['Show helper']))

    def test_receive_labelled_as_simulation(self):
        h = page()
        self.assertIn('<button data-go="receive">Simulate incoming text</button>', h)
        self.assertNotIn('<button data-go="receive">Receive</button>', h)
