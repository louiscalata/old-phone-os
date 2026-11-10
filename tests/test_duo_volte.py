# SPDX-License-Identifier: GPL-3.0-or-later
"""The VoLTE note stays an open check."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Volte(unittest.TestCase):
    def test_the_note_is_not_a_result(self):
        text = (ROOT / "docs/duo-volte.md").read_text(encoding="utf-8")
        self.assertIn("NOT_RUN", text)
        self.assertIn("ofono-binder-plugin-ext-qti", text)
        self.assertNotIn("is installed", text.lower())
        self.assertNotIn("ims registers", text.lower())
        for banned in ("imei", "imsi", "iccid", "serial"):
            self.assertNotIn(banned, text.lower())
