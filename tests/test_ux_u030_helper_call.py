# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


class HelperCallTest(unittest.TestCase):
    def test_helper_estimate_per_action(self):
        src = (pathlib.Path(__file__).resolve().parent.parent / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("localStorage.getItem('opos_helper_s_'+o.action)"), 1)
        self.assertEqual(src.count("localStorage.setItem('opos_helper_s_'+o.action,String(s))"), 1)
        self.assertNotIn("localStorage.getItem('opos_helper_s')", src)
        self.assertNotIn("localStorage.setItem('opos_helper_s',String(s))", src)
        self.assertIn("let expected=60", src)
        self.assertIn("Math.max(5,Math.min(300,v))", src)
