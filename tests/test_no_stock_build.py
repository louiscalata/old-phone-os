# SPDX-License-Identifier: GPL-3.0-or-later
"""The stock-build and bootloader strings stay out of the tree. The check stores hashes, not the strings."""
import unittest
from pathlib import Path

from tests.stock_build_guard import hits, tracked_texts

ROOT = Path(__file__).resolve().parents[1]


class NoStockBuild(unittest.TestCase):
    def test_the_tree_has_none_of_the_banned_hashes(self):
        bad = []
        for path, text in tracked_texts(ROOT):
            if hits(text):
                bad.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(bad, [])
