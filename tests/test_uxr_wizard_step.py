# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import opos.setup.wizard_page


class TestWizardStepText(unittest.TestCase):
    def setUp(self):
        self.html = opos.setup.wizard_page.page("tok", "testnonce123")

    def test_step_line_visible_before_first_step(self):
        self.assertEqual(self.html.count('id="stepno"'), 1)
        pos = self.html.find('id="stepno"')
        start = self.html.rfind("<", 0, pos)
        end = self.html.find(">", pos)
        tag = self.html[start:end + 1]
        self.assertIn('class="muted"', tag)
        self.assertNotIn("hidden", tag)
        err = self.html.find('id="error"')
        s1 = self.html.find('id="s1"')
        self.assertNotEqual(err, -1)
        self.assertNotEqual(pos, -1)
        self.assertNotEqual(s1, -1)
        self.assertLess(err, pos)
        self.assertLess(pos, s1)

    def test_updmeter_writes_step_text(self):
        start = self.html.find("function updMeter(){")
        end = self.html.find("function show(i){", start)
        body = self.html[start:end]
        self.assertIn("#stepno", body)
        self.assertIn("textContent", body)
        self.assertIn('"Step "', body)
        self.assertIn('" of "', body)
        self.assertIn("cur+1", body)
        self.assertIn("steps.length", body)
        self.assertNotIn("innerHTML", body)
        self.assertIn("aria-valuenow", body)

    def test_seven_steps(self):
        self.assertEqual(self.html.count('class="step panel"'), 7)


if __name__ == "__main__":
    unittest.main()
