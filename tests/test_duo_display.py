# SPDX-License-Identifier: GPL-3.0-or-later
"""Both Duo panels take one raw brightness from the vendor curve."""
import tempfile
import unittest
from pathlib import Path

from opos.display import CURVE, apply, panel_value


def panels(root):
    for name, raw in (("panel0-backlight", "90"), ("panel1-backlight", "200")):
        path = Path(root) / "sys/class/backlight" / name
        path.mkdir(parents=True)
        (path / "brightness").write_text(raw + "\n", encoding="utf-8")


class Curve(unittest.TestCase):
    def test_knots_floor_and_cap(self):
        for level, raw in CURVE:
            self.assertEqual(panel_value(level), raw, level)
        self.assertEqual(panel_value(0), 3)
        self.assertEqual(panel_value(1), 3)
        self.assertEqual(panel_value(255), 245)
        self.assertEqual(panel_value(999), 245)
        self.assertEqual(panel_value("nope"), 3)

    def test_between_knots(self):
        # 20 -> 22, 40 -> 48, so 30 is halfway: 35.
        self.assertEqual(panel_value(30), 35)


class Apply(unittest.TestCase):
    def test_both_panels_match_and_the_restore_file_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            panels(tmp)
            runtime = Path(tmp) / "run"
            value = apply(tmp, 128, runtime)
            self.assertEqual(value, 147)
            for name in ("panel0-backlight", "panel1-backlight"):
                text = (Path(tmp) / "sys/class/backlight" / name / "brightness").read_text(encoding="utf-8")
                self.assertEqual(text, "147\n")
            self.assertEqual((runtime / "sfduo-brightness").read_text(encoding="utf-8"), "147\n")

    def test_a_missing_panel_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sys/class/backlight/panel0-backlight"
            path.mkdir(parents=True)
            (path / "brightness").write_text("1\n", encoding="utf-8")
            self.assertEqual(apply(tmp, 90, None), 107)
            self.assertEqual((path / "brightness").read_text(encoding="utf-8"), "107\n")


if __name__ == "__main__":
    unittest.main()
