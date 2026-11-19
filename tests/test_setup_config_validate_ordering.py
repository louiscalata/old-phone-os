# SPDX-License-Identifier: GPL-3.0-or-later
"""Preserve exact diagnostic and patched-global call order across hash seeds."""

import json
import unittest
from pathlib import Path

from opos.setup import config
from tests.fixtures.setup_config_validate import support

CORPUS_PATH = Path(__file__).parent / "fixtures" / "setup_config_validate" / "cases.json"
DOCUMENT = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


class ValidateOrderingTests(unittest.TestCase):
    def test_recorded_hash_seeds(self):
        for seed in DOCUMENT["generated_from"]["hash_seeds"]:
            if seed == "0":
                continue
            with self.subTest(seed=seed):
                outcomes = support.replay_cases(config, CORPUS_PATH, seed)
                self.assertEqual(len(outcomes), len(DOCUMENT["cases"]))
                for case, actual in zip(DOCUMENT["cases"], outcomes, strict=True):
                    with self.subTest(seed=seed, case=case["name"]):
                        self.assertEqual(actual, support.expected_outcome(case, seed))


if __name__ == "__main__":
    unittest.main()
