# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
spec = importlib.util.spec_from_file_location('check', os.path.join(ROOT, 'guard', 'check.py'))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

LISTED = ('fix-F3013a', 'fix-F3013b', 'fix-F3014a', 'fix-F3014b', 'fix-F3017', 'fix-F2502', 'fix-F3020', 'fix-F3021', 'fix-F3030', 'fix-F3031', 'fix-F231', 'fix-F2996')


class TestGuardFrontierException(unittest.TestCase):
    def test_listed_ids_pass_with_model_on_their_own_line(self):
        for pid in LISTED:
            self.assertTrue(check.frontier_ok(pid, [f'- {pid}: x; written by Sol 6.1']), pid)
            self.assertTrue(check.frontier_ok(pid, [f'- {pid}: x; cross-reviewed by Opus 5.5']), pid)

    def test_model_on_a_different_line_fails(self):
        self.assertFalse(check.frontier_ok('fix-F3017', ['- fix-F3017: x; written by a model', '- fix-F2502: y; written by Sol 6.1']))

    def test_unlisted_ids_fail_even_with_a_model_on_their_line(self):
        for pid in ('fix-F3015', 'fix-F3013', 'fix-F3013c', 'fix-F30130', 'fix-F3017-x', 'fix-F3017.x', 'fix-F3017:1',
                    'fix-F3017/x', 'fix-F3020.F9999', 'fix-F2541', 'uiux-UX01', 'fix-F25020', 'fix-F2310', 'fix-F23', 'fix-F2996.1', 'fix-F29961'):
            self.assertFalse(check.frontier_ok(pid, [f'- {pid}: x; written by Sol 6.1']), pid)

    def test_model_names_are_explicit(self):
        self.assertTrue(check.FRONTIER_MODEL.search('written by Sol 6.1 via the executor'))
        self.assertTrue(check.FRONTIER_MODEL.search('written by Opus 5.5'))
        self.assertTrue(check.FRONTIER_MODEL.search('written by Opus 5.5.'))
        self.assertFalse(check.FRONTIER_MODEL.search('written by a model'))
        for bad in ('Sol 6.10', 'Sol 6.1.9', 'Opus 5.55'):
            self.assertFalse(check.FRONTIER_MODEL.search(bad), bad)

    def test_meta_rule_unchanged(self):
        self.assertTrue(check.META.search('written by muse-glimmer-30b'))
        self.assertFalse(check.META.search('written by Sol 6.1'))
