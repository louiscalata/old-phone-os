# SPDX-License-Identifier: GPL-3.0-or-later
"""Factory and connection lifecycle outputs recorded before any source split."""

import unittest

from tests.fixtures.setup_server_golden.support import corpus_cases, run_case


class SetupServerLifecycleGoldenTests(unittest.TestCase):
    """Keep admission, timeout equality and exceptional cleanup observable."""


def golden_test(case):
    def test(self):
        self.assertEqual(run_case(case), case["expected"])

    return test


for _case in corpus_cases("lifecycle"):
    _name = "test_" + _case["name"].replace(".", "_").replace("-", "_")
    if hasattr(SetupServerLifecycleGoldenTests, _name):
        raise ValueError(f"Duplicate golden case: {_name}")
    setattr(SetupServerLifecycleGoldenTests, _name, golden_test(_case))


if __name__ == "__main__":
    unittest.main()
