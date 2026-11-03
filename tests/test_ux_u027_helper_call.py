# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class TestHelperCallWording(unittest.TestCase):
    def test_unreachable_wording(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        text = (root / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        self.assertIn("return{error:'OPOS is not responding. Wait a few seconds, reload this page, then try again.'}", text)
        self.assertNotIn("The helper could not be reached.", text)
