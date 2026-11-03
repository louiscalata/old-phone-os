# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest

import opos.web


def _one(lines, marker):
    found = [ln for ln in lines if marker in ln]
    assert len(found) == 1, (marker, len(found))
    return found[0]


class HelperHintTest(unittest.TestCase):
    def test_page_maps_helper_hidden_to_hint(self):
        lines = opos.web.page().splitlines()
        # The inline Ask/Plan/Why handlers go through helperCall(); UXP31's Helper screen (phone-b.js) sends its own
        # action:'ask' request with its own error copy, so the marker names the helperCall line explicitly.
        targets = {
            "ask": _one(lines, "document.getElementById('askbtn').onclick"),
            "plan": _one(lines, "action:'plan'"),
            "why": _one(lines, "action:'explain'"),
        }
        for name, line in targets.items():
            with self.subTest(handler=name):
                self.assertIn("r.error==='helper hidden'", line)
                self.assertIn("Tap Show helper first", line)
                self.assertIn(":r.error", line)
                self.assertNotIn("ans.textContent=r.error;", line)

    def test_server_error_text_unchanged(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        text = (root / "src" / "opos" / "web.py").read_text(encoding="utf-8")
        self.assertEqual(text.count('self._json({"error": "helper hidden"})'), 3)


if __name__ == "__main__":
    unittest.main()
