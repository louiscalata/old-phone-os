# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect legacy review suites whose hyphenated filenames discovery rejects.

Import their original module names so case IDs and mock targets stay unchanged.
"""
import importlib
import unittest


_REVIEW_MODULES = (
    "tests.test_10-ssh_review",
    "tests.test_20-firewall_review",
    "tests.test_harden-device_review",
)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(tests)
    for name in _REVIEW_MODULES:
        module = importlib.import_module(name)
        suite.addTests(loader.loadTestsFromModule(module, pattern=pattern))
    return suite
