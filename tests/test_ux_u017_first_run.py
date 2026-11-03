# SPDX-License-Identifier: GPL-3.0-or-later
import re
import unittest

from tests.web_source import web_text


COPY = "Old Phone OS keeps calls and messages working. Pull down from the top edge for details."


class TestIntro(unittest.TestCase):
    def test_intro_present(self):
        src = web_text()
        want = '<p id="intro" class="box">' + COPY + "</p>"
        self.assertEqual(src.count(want), 1)
        lines = [line.strip() for line in src.splitlines()]
        idx = lines.index(want)
        self.assertIn('id="notice"', lines[idx - 1])
        self.assertIn('id="contacts"', lines[idx + 1])
        found = re.search(r'<p id="intro" class="box">(.*?)</p>', src, re.DOTALL)
        self.assertIsNotNone(found)
        text = found.group(1)
        self.assertEqual(text, COPY)
        self.assertNotIn("Battery", text)
        self.assertNotIn("battery", text)
        self.assertNotIn("%", text)
