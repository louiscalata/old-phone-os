# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
spec = importlib.util.spec_from_file_location('check', os.path.join(ROOT, 'guard', 'check.py'))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

SUFFIXES = ('', '-fixup', '-guard', '-guardwiden', '-guardfix', '-r1-astra')
LISTED = tuple(f'uiux-UX0{n}{s}' for n in range(1, 6) for s in SUFFIXES)
UXP_LISTED = ('uiux-UXP00', 'uiux-UXP99', 'uiux-UXP21-fixup', 'uiux-UXP20-fix4', 'uiux-UXP20-fix12', 'uiux-UXP01-correction',
              'uiux-UXP09-guard', 'uiux-UXP12-r1b', 'uiux-UXP12-r0f', 'uiux-UXP12-r2a-fixup', 'uiux-UXP12-r9a-fix07',
              'uiux-UXP12-r3c-correction', 'uiux-UXP12-r1b-guard')
UXP_BAD = ('uiux-UXP1', 'uiux-UXP100', 'uiux-UXP20-evil', 'uiux-UXP20-fix123', 'uiux-UXP20-fixup-fixup', 'uiux-UXP20-fix',
           'uiux-UXP20-r1g', 'uiux-UXP20-r1', 'uiux-UXP20-r10a', 'uiux-UXP20-r1b-r2a', 'uiux-UXP20-fixup-r1b',
           'uiux-UXP20-guardwiden', 'uiux-UXP20-r1-astra', 'uiux-UXP20-', 'uiux-UXPab', 'uiux-UX01-correction',
           'uiux-UX01-r1b', 'uiux-UXP20/F9999', 'xuiux-UXP20', 'uiux-UX06')
UXF_LISTED = ('uiux-UXF01', 'uiux-UXF21-fixup', 'uiux-UXF05-fix3', 'uiux-UXF02-r1b', 'uiux-UXF03-correction',
              'uiux-UXF04-guard', 'uiux-UXF12-r2a-fix07')
UXF_BAD = ('uiux-UXF1', 'uiux-UXF100', 'uiux-UXF01-evil', 'uiux-UXF01-fixup-fixup', 'uiux-UXG01', 'fable-UXF01',
           'uiux-UXF01-fix123', 'uiux-UXF01-r1', 'uiux-UXF01-guardwiden', 'uiux-UXF01-r1-astra', 'uiux-UXF01-',
           'uiux-UXF01/F9999', 'xuiux-UXF01', 'uiux-uxf01')
BAD_IDS = ('uiux-UX01evil', 'uiux-UX05/F9999', 'uiux-UX06', 'uiux-UX01-fixup2', 'uiux-UX00', 'uiux-UX0x',
           'uiux-UX01-', 'uiux-UX01-fixup-guard', 'xuiux-UX01', 'fix-F3030')


class TestGuardUiuxException(unittest.TestCase):
    def test_id_allowlist_fullmatch(self):
        for pid in LISTED:
            self.assertTrue(check.UIUX_ID.fullmatch(pid), pid)
        for pid in BAD_IDS:
            self.assertFalse(check.UIUX_ID.fullmatch(pid), pid)

    def test_uxp_round_ids(self):
        for pid in UXP_LISTED:
            self.assertTrue(check.UIUX_ID.fullmatch(pid), pid)
            self.assertTrue(check.uiux_ok(pid, [f'- {pid}: x; written by Opus 5.5']), pid)
            self.assertFalse(check.uiux_ok(pid, [f'- {pid}: x; written by Opus 5.5.1']), pid)
        for pid in UXP_BAD:
            self.assertFalse(check.UIUX_ID.fullmatch(pid), pid)
            self.assertFalse(check.uiux_ok(pid, [f'- {pid}: x; written by Astra']), pid)

    def test_uxf_fable_ids(self):
        for pid in UXF_LISTED:
            self.assertTrue(check.UIUX_ID.fullmatch(pid), pid)
            self.assertTrue(check.uiux_ok(pid, [f'- {pid}: x; written by Fable']), pid)
            self.assertTrue(check.uiux_ok(pid, [f'- {pid}: x; written by Fable (Claude Code)']), pid)
            self.assertFalse(check.uiux_ok(pid, [f'- {pid}: x; written by Fable2']), pid)
            self.assertFalse(check.uiux_ok(pid, [f'- {pid}: x; written by a model', '- uiux-UXF99: y; written by Fable']), pid)
        for pid in UXF_BAD:
            self.assertFalse(check.UIUX_ID.fullmatch(pid), pid)
            self.assertFalse(check.uiux_ok(pid, [f'- {pid}: x; written by Fable']), pid)

    def test_model_names_are_explicit(self):
        for good in ('written by Astra', 'written by Opus 5.5', 'written by Opus 5.5.', 'Astra via the executor',
                     'written by Fable', 'written by Fable (Claude Code)', 'Fable.'):
            self.assertTrue(check.UIUX_MODEL.search(good), good)
        for bad in ('Opus 5.5.1', 'Opus 5.55', 'Sol 6.1', 'Astral', 'written by a model', 'Fable2', 'Fabled',
                    'Fable5.1', 'Fable.1', 'Fables', 'fable', 'Fable 5.2', 'Fable 99', 'Fable-v2', 'Fable v2'):
            self.assertFalse(check.UIUX_MODEL.search(bad), bad)

    def test_listed_ids_pass_with_model_on_their_own_line(self):
        for pid in LISTED:
            self.assertTrue(check.uiux_ok(pid, [f'- {pid}: x; written by Astra']), pid)
            self.assertTrue(check.uiux_ok(pid, [f'- {pid}: x; written by Opus 5.5']), pid)

    def test_bad_ids_fail_even_with_a_model_on_their_line(self):
        for pid in BAD_IDS:
            self.assertFalse(check.uiux_ok(pid, [f'- {pid}: x; written by Astra']), pid)

    def test_bad_model_versions_fail(self):
        for model in ('Opus 5.5.1', 'Opus 5.55', 'Sol 6.1'):
            self.assertFalse(check.uiux_ok('uiux-UX01', [f'- uiux-UX01: x; written by {model}']), model)

    def test_model_on_a_different_line_fails(self):
        self.assertFalse(check.uiux_ok('uiux-UX02', ['- uiux-UX02: x; written by a model', '- uiux-UX03: y; written by Astra']))

    def test_frontier_and_meta_rules_unchanged(self):
        self.assertTrue(check.frontier_ok('fix-F3030', ['- fix-F3030: x; written by Sol 6.1']))
        self.assertFalse(check.frontier_ok('uiux-UX01', ['- uiux-UX01: x; written by Sol 6.1']))
        self.assertTrue(check.META.search('written by muse-glimmer-30b'))
        self.assertFalse(check.META.search('written by Astra'))


if __name__ == '__main__':
    unittest.main()
