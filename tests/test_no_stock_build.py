# SPDX-License-Identifier: GPL-3.0-or-later
"""The stock-build and bootloader strings stay out of the tree. The check stores hashes, not the strings."""
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from tests import stock_build_guard
from tests.stock_build_guard import hits, tracked_texts

ROOT = Path(__file__).resolve().parents[1]
# A made-up token. It is not a device identifier. Its hash is computed here and planted for one assertion.
PROBE = "guard-probe-token"


class NoStockBuild(unittest.TestCase):
    def test_the_tree_has_none_of_the_banned_hashes(self):
        bad = []
        for path, text in tracked_texts(ROOT):
            if hits(text):
                bad.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(bad, [])

    def test_a_planted_hash_is_caught(self):
        digest = hashlib.sha256(PROBE.encode("utf-8")).hexdigest()
        planted = {len(PROBE): frozenset({digest})}
        with mock.patch.dict(stock_build_guard.BANNED, planted, clear=True):
            self.assertEqual(hits("prefix " + PROBE + " suffix"), [digest])
            self.assertEqual(hits("xx" + PROBE + "yy"), [digest])
            self.assertEqual(hits("nothing to catch here"), [])
            self.assertEqual(hits(""), [])
