# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest

from tests.web_source import web_text

LABELS = ["Battery", "Start simulation", "Show helper", "Why?", "Plan", "Ask"]


class DemoScriptControlsTest(unittest.TestCase):
    def test_demo_labels_in_page(self):
        text = web_text()
        for label in LABELS:
            self.assertIn(label, text, label)

    def test_quickstart_mentions_demo_labels(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        text = (root / "README.md").read_text(encoding="utf-8")
        start = text.find("## Quickstart")
        self.assertNotEqual(start, -1, "README Quickstart missing")
        rest = text[start:]
        cut = rest.find("\n## ", 1)
        section = rest[:cut] if cut != -1 else rest
        for label in LABELS:
            self.assertIn(label, section, label)

    def test_demo_query_param(self):
        self.assertIn('"demo"', web_text(), '"demo"')


if __name__ == "__main__":
    unittest.main()
