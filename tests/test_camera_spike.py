# SPDX-License-Identifier: GPL-3.0-or-later
"""The camera note is research only: no module, and the still is NOT_RUN."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CameraSpike(unittest.TestCase):
    def test_the_note_says_not_run_and_there_is_no_camera_module(self):
        text = (ROOT / "docs" / "camera-spike.md").read_text(encoding="utf-8")
        self.assertIn("NOT_RUN", text)
        self.assertIn("No capture command was run", text)
        self.assertIn("gst-launch-1.0 droidcamsrc", text)
        self.assertFalse((ROOT / "src" / "opos" / "camera.py").exists())
        self.assertNotIn("Take photo", "\n".join(p.name for p in (ROOT / "src" / "opos").glob("*.py")))


if __name__ == "__main__":
    unittest.main()
