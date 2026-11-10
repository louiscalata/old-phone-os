# SPDX-License-Identifier: GPL-3.0-or-later
"""The gap note stays factual: device checks are NOT_RUN, and service work is listed only."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "daily-phone-gaps.md"


class GapNote(unittest.TestCase):
    def test_the_note_names_the_work_and_does_not_claim_a_phone_run(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("NOT_RUN", text)
        self.assertIn("99,973", text)
        self.assertIn("102,000", text)
        for phrase in (
            "oFono",
            "Call log",
            "Flashlight",
            "stopwatch",
            "Storage readout",
            "Do Not Disturb",
            "Unit converter",
            "Listed only.",
            "clock,contacts,notes,calc",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("tested on the Duo", text)
        self.assertNotIn("IMEI", text)
        self.assertIn("daily-phone-gaps.md", (ROOT / "docs" / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
