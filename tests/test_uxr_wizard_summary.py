# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import opos.setup.wizard_page

class TestUxrWizardSummary(unittest.TestCase):
    def setUp(self):
        h = opos.setup.wizard_page.page("t"*32, "n")
        start = h.index("function buildSummary(){")
        end = h.index("function ", start + len("function buildSummary(){"))
        self.body = h[start:end]
        self.h = h

    def test_summary_uses_service_labels(self):
        body = self.body
        self.assertIn("Messages", body)
        self.assertIn("Alarms", body)
        self.assertIn("Apps you use", body)
        self.assertNotIn('c.services.join(", ")', body)

    def test_summary_uses_battery_labels(self):
        body = self.body
        self.assertIn("Work it out for me", body)
        self.assertIn("My battery is worn", body)
        self.assertIn("My battery is fine", body)
        self.assertNotIn('["Battery",c.battery_mode]', body)

    def test_summary_has_no_raw_placeholders(self):
        body = self.body
        self.assertIn('"Off"', body)
        self.assertIn('"Added"', body)
        self.assertIn('"None"', body)
        self.assertNotIn('"off"', body)
        self.assertNotIn('"set"', body)
        self.assertNotIn('"none"', body)

    def test_form_values_unchanged(self):
        h = self.h
        self.assertIn('value="sms"', h)
        self.assertIn('value="worn"', h)
        self.assertIn('value="auto"', h)

if __name__ == "__main__":
    unittest.main()
