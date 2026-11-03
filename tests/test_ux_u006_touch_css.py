# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest

from tests.web_source import web_text


class TestMeterHitTouchCSS(unittest.TestCase):
    def test_meter_hit_48px(self):
        text = web_text()
        hit = re.search(r"#meter-hit\{[^}]*\}", text)
        after = re.search(r"#meter-hit::after\{[^}]*\}", text)
        self.assertIsNotNone(hit)
        self.assertIsNotNone(after)
        self.assertIn("height:48px", hit.group(0))
        self.assertNotIn("height:24px", hit.group(0))
        top = int(re.search(r"top:(\d+)px", after.group(0)).group(1))
        height = int(re.search(r"[;{]height:(\d+)px", after.group(0)).group(1))
        self.assertLessEqual(top + height, 8, "the pill sits above the 28px status bar's text line, so it never crosses the helper label or the light-theme status text")
        self.assertGreaterEqual(top, 4, "the pill clears the 4px battery line at the top edge")
        self.assertIn("width:40px", after.group(0))
        self.assertIn("height:4px", after.group(0))
        self.assertNotIn("top:8px", after.group(0))


if __name__ == "__main__":
    unittest.main()
