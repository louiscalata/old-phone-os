# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep known retired Duo identifiers out of the tracked source."""

import hashlib
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(rb"[A-Za-z0-9_./:=-]{8,}")
RETIRED = {
    11: "1108c4f05db60c0febd22ce6c0ef53c91e72d33ca73a23617febb928fcc12fcd",
    12: "0d92293a046c124bb0971dc0ee92b88afb68d6e808b48a48d8340a215ade9b7c",
    32: "3e5d108abd3a7cb4652255338b47ea6b1114b961ef0020bc1caabeb507a1012d",
    36: "01cccced2e99a10fa4c61150bae94b401253485e126a13e09a9bc07bf4fba517",
}


def contains_retired_identifier(data, retired):
    for match in TOKEN.finditer(data):
        token = match.group()
        for length, digest in retired.items():
            for start in range(len(token) - length + 1):
                if hashlib.sha256(token[start : start + length]).hexdigest() == digest:
                    return True
    return False


class TestRetiredStockIdentifiers(unittest.TestCase):
    def test_no_retired_identifiers_in_tracked_text(self):
        files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
        matches = []
        for name in filter(None, files):
            path = ROOT / os.fsdecode(name)
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if b"\0" not in data and contains_retired_identifier(data, RETIRED):
                matches.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(matches, [])

    def test_a_retired_token_is_detected_inside_a_larger_token(self):
        example = b"synthetic-guard-value"
        retired = {len(example): hashlib.sha256(example).hexdigest()}
        self.assertTrue(contains_retired_identifier(b"prefix=" + example + b"-suffix", retired))
        self.assertFalse(contains_retired_identifier(b"prefix=example-suffix", retired))
