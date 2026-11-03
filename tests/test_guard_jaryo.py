# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
spec = importlib.util.spec_from_file_location('check', os.path.join(ROOT, 'guard', 'check.py'))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

GOOD = ('uiux-JRY00', 'uiux-JRY99', 'uiux-JRY12-fixup', 'uiux-JRY12-fix3', 'uiux-JRY12-fix10')
BAD = ('uiux-JRY1', 'uiux-JRY100', 'uiux-JRY12-evil', 'uiux-JRY12-fix', 'uiux-JRY12-fix123', 'uiux-JRY12-fixup-fixup',
       'uiux-JRY12-', 'uiux-JRYab', 'xuiux-JRY12', 'uiux-JRY12/F9999', 'jry-JRY12', 'uiux-UX01', 'fix-F3030')


class TestGuardJaryoException(unittest.TestCase):
    def test_id_allowlist_is_closed(self):
        for pid in GOOD:
            self.assertTrue(check.JRY_ID.fullmatch(pid), pid)
        for pid in BAD:
            self.assertFalse(check.JRY_ID.fullmatch(pid), pid)

    def test_models_named_explicitly(self):
        for good in ('written by Opus 5.5', 'written by Sol 6.1.', 'written by Fable', 'Astra via the executor'):
            self.assertTrue(check.JRY_MODEL.search(good), good)
        for bad in ('Opus 5.5.1', 'Sol 6.15', 'Fabled', 'written by a model', 'Opus 5.55'):
            self.assertFalse(check.JRY_MODEL.search(bad), bad)

    def test_existing_exceptions_unchanged(self):
        self.assertTrue(check.UIUX_ID.match('uiux-UX01'))
        self.assertTrue(check.UIUX_PHONE_ID.match('uiux-UXP20'))
        self.assertFalse(check.UIUX_ID.match('uiux-JRY00'))
        self.assertTrue(check.META.search('written by muse-glimmer-30b'))
        self.assertFalse(check.META.search('written by Fable'))


if __name__ == '__main__':
    unittest.main()
