# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from opos.web import page
class TestButtonSpacing(unittest.TestCase):
    def test_buttons_have_gap(self):
        html = page()
        self.assertIn("button{min-height:44px;margin-right:8px}", html)
