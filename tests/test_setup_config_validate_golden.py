# SPDX-License-Identifier: GPL-3.0-or-later
"""Full validator outcomes generated from the unchanged baseline implementation."""

import json
import unittest
from pathlib import Path

from opos.setup import config
from tests.fixtures.setup_config_validate import support

CORPUS_PATH = Path(__file__).parent / "fixtures" / "setup_config_validate" / "cases.json"
DOCUMENT = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
CASES = DOCUMENT["cases"]


class ValidateGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outcomes = support.replay_cases(config, CORPUS_PATH, "0")
        if len(cls.outcomes) != len(CASES):
            raise ValueError("Incomplete validator replay")


def golden_test(index, case):
    def test(self):
        self.assertEqual(self.outcomes[index], support.expected_outcome(case, "0"))

    return test


for _index, _case in enumerate(CASES):
    _name = "test_" + _case["name"]
    if hasattr(ValidateGoldenTests, _name):
        raise ValueError(f"Duplicate golden case: {_name}")
    setattr(ValidateGoldenTests, _name, golden_test(_index, _case))


if __name__ == "__main__":
    unittest.main()
