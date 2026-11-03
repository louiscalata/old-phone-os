# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.web_source import web_text


class TouchTargets(unittest.TestCase):
    def test_slider_checkbox_targets(self):
        src = web_text()
        self.assertIn("input[type=range]{min-height:44px}", src)
        self.assertIn("input[type=checkbox]{width:24px;height:24px}", src)
