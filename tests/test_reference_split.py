# SPDX-License-Identifier: GPL-3.0-or-later
"""The hackathon tree is labeled as a reference, and the Surface OS is not claimed to live here."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTE = (
    "This was a hackathon project (Meta hackathon, Oct 2026, Indie developer track). "
    "It is kept as a reference; the current OPOS interface is not in this repository."
)


class TestReferenceSplit(unittest.TestCase):
    def test_reference_note(self):
        text = (ROOT / "reference" / "meta-hackathon" / "README.md").read_text(encoding="utf-8")
        self.assertIn(NOTE, text)
        self.assertIn("src/opos/web.py", text)
        self.assertIn("data-ff=foldable", text)
        self.assertIn("hardware dossier", text.lower())

    def test_readme_leads_with_the_missing_os(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        head = readme.split("## Support", 1)[0]
        self.assertIn("That interface is not in this repository.", head)
        self.assertIn("reference/meta-hackathon/README.md", head)
        self.assertIn("## Quickstart", readme)
        self.assertIn("This quickstart starts the hackathon reference interface.", readme)

    def test_release_notes_do_not_present_the_demo_as_the_os(self):
        notes = (ROOT / "docs" / "release-notes" / "v0.2.0.md").read_text(encoding="utf-8")
        self.assertIn("That interface is not in this repository", notes)
        self.assertIn("Meta hackathon reference", notes)
        self.assertNotIn("docs/release-notes-v0.2.0.md", notes)


if __name__ == "__main__":
    unittest.main()
