# SPDX-License-Identifier: GPL-3.0-or-later
"""Full HTTP outputs and ordered effects recorded from the original setup factory."""

import unittest
from unittest import mock

from opos import notices
from opos.setup import server
from tests.fixtures.setup_server_golden.support import corpus_cases, run_case


class SetupServerGoldenTests(unittest.TestCase):
    """Keep request routing, refusal precedence and persistence observable."""

    # Page bytes are recorded from the original source. Legitimate page changes
    # require regeneration from the intended baseline and an explanation here.
    # The portability repair keeps the real page bytes unchanged; only notice
    # version/build metadata is pinned to the values of the recorded baseline.


class SetupServerEnvironmentTests(unittest.TestCase):
    """Release metadata and stdlib templates do not define factory behavior."""

    def test_driver_isolates_release_metadata(self):
        case = next(case for case in corpus_cases("http") if case["name"] == "get_route_105")
        with mock.patch.object(notices, "VERSION", "99.8.7"), mock.patch.object(notices, "BUILD", "abc1234"):
            self.assertEqual(run_case(case), case["expected"])
            self.assertEqual(notices.VERSION, "99.8.7")
            self.assertEqual(notices.BUILD, "abc1234")

    def test_unsupported_methods_ignore_stdlib_error_template(self):
        cases = [case for case in corpus_cases("http") if case["name"] in ("method_TRACE", "method_CONNECT")]
        for template in ("<html>Old runtime error</html>", "<html>New runtime error with style</html>"):
            with mock.patch.object(server.BaseHTTPRequestHandler, "error_message_format", template):
                for case in cases:
                    with self.subTest(template=template, method=case["name"]):
                        self.assertEqual(run_case(case), case["expected"])


def golden_test(case):
    def test(self):
        self.assertEqual(run_case(case), case["expected"])

    return test


for _case in corpus_cases("http"):
    _name = "test_" + _case["name"].replace(".", "_").replace("-", "_")
    if hasattr(SetupServerGoldenTests, _name):
        raise ValueError(f"Duplicate golden case: {_name}")
    setattr(SetupServerGoldenTests, _name, golden_test(_case))


if __name__ == "__main__":
    unittest.main()
