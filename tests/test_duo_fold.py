# SPDX-License-Identifier: GPL-3.0-or-later
"""Fold soft-blank from an SW_LID fixture. The compositor is not switched off."""
import unittest
from pathlib import Path

from opos.fold import plan, read_lid

ROOT = Path(__file__).resolve().parents[1]


class Fold(unittest.TestCase):
    def test_lid_values(self):
        self.assertIsNone(read_lid(""))
        self.assertTrue(read_lid("code 0 (SW_LID), value 1"))
        self.assertFalse(read_lid("SW_LID 1\nSW_LID 0"))

    def test_soft_blank_and_restore(self):
        blank = plan(True, 128, 1000)
        self.assertEqual(blank, {"blank": True, "brightness": 3, "poll_ms": 10000, "page": "black"})
        self.assertEqual(plan(False, 128, 1000), {"blank": False, "brightness": 128, "poll_ms": 1000, "page": "restore"})

    def test_the_compositor_is_not_mentioned(self):
        text = (ROOT / "src/opos/fold.py").read_text(encoding="utf-8")
        self.assertNotIn("wlr-randr", text)
        self.assertNotIn("--off", text)
