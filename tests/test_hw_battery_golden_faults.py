# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin unreadable files, helper faults and escaping battery-reader exceptions."""

import json
import unittest

from opos.hw import battery
from tests.fixtures.hw_battery_golden_support import load_cases, run_case


class BatteryGoldenFaultTests(unittest.TestCase):
    """Keep partial-state and exception behavior observable before splitting."""


def _golden_test(case):
    def test(self):
        actual = run_case(battery, case)
        self.assertEqual(
            json.dumps(actual, sort_keys=True, allow_nan=False),
            json.dumps(case["expected"], sort_keys=True, allow_nan=False),
        )

    return test


for _case in load_cases("faults"):
    _name = "test_" + _case["name"]
    if hasattr(BatteryGoldenFaultTests, _name):
        raise ValueError(f"Duplicate battery golden case: {_name}")
    setattr(BatteryGoldenFaultTests, _name, _golden_test(_case))


if __name__ == "__main__":
    unittest.main()
