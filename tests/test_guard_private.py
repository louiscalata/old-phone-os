# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import pathlib
import unittest

_guard_path = pathlib.Path(__file__).resolve().parents[1] / "guard" / "check.py"
_spec = importlib.util.spec_from_file_location("opos_guard_check", _guard_path)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

HANDLE = 'louis' + 'cal' + 'ata'
PAT = mod.re.compile(r'(?i)louis[\s._-]*' + 'cal' + 'ata')


class TestGuardPrivate(unittest.TestCase):
    def test_extra_patterns_compile(self):
        pats = mod.extra_patterns({'private_extra': ['foo[0-9]+']})
        self.assertEqual(len(pats), 1)
        self.assertTrue(pats[0].search('xx foo12'))
        self.assertIsNone(pats[0].search('bar'))

    def test_extra_patterns_empty(self):
        self.assertEqual(mod.extra_patterns({}), [])
        self.assertEqual(mod.extra_patterns({'private_extra': [1, '']}), [])

    def test_home_path_matches(self):
        self.assertTrue(any(p.search('/' + 'Users/abc/x') for p in mod.PRIVATE))

    def test_private_network_and_home_path_coverage(self):
        dot = chr(46)
        sl = chr(47)
        bs = chr(92)
        ho = 'ho' + 'me'
        us = 'Us' + 'ers'
        un = 'a' + 'bc'
        fn = 'x'
        drv = 'C' + ':'
        good = [(10, 0, 0, 0), (10, 1, 2, 3), (10, 255, 255, 255), (172, 16, 0, 0), (172, 20, 1, 2), (172, 31, 255, 255), (192, 168, 0, 0), (192, 168, 255, 255)]
        bad = [(9, 255, 255, 255), (11, 0, 0, 0), (110, 1, 2, 3), (172, 15, 255, 255), (172, 32, 0, 0), (192, 167, 255, 255), (192, 169, 0, 0), (192, 0, 2, 1), (198, 51, 100, 1), (203, 0, 113, 1)]
        n = 0
        for t in good:
            s = dot.join(str(o) for o in t)
            with self.subTest(n):
                self.assertTrue(any(p.search(s) for p in mod.PRIVATE))
            n += 1
            pre = str(7) + dot + s
            with self.subTest(n):
                self.assertFalse(any(p.search(pre) for p in mod.PRIVATE))
            n += 1
            post = s + dot + str(7)
            with self.subTest(n):
                self.assertFalse(any(p.search(post) for p in mod.PRIVATE))
            n += 1
            over = dot.join(str(o) for o in t[:3]) + dot + str(256)
            with self.subTest(n):
                self.assertFalse(any(p.search(over) for p in mod.PRIVATE))
            n += 1
        for t in bad:
            s = dot.join(str(o) for o in t)
            with self.subTest(n):
                self.assertFalse(any(p.search(s) for p in mod.PRIVATE))
            n += 1
        oks = [sl + ho + sl + un + sl + fn, sl + ho + sl + un, sl + us + sl + un + sl + fn, drv + bs + us + bs + un + bs + fn, drv + bs + bs + us + bs + bs + un + bs + bs + fn]
        for s in oks:
            with self.subTest(n):
                self.assertTrue(any(p.search(s) for p in mod.PRIVATE))
            n += 1
        kos = [sl + 'fa' + 'ke' + sl + ho + sl + un + sl + fn, sl + ho + sl + 'a' + sl + fn, 'hello world']
        for s in kos:
            with self.subTest(n):
                self.assertFalse(any(p.search(s) for p in mod.PRIVATE))
            n += 1

    def test_clean_text_passes(self):
        self.assertFalse(any(p.search("Hardware: a Windows PC, Python 3.13") for p in mod.PRIVATE))

    def test_no_guard_json_skips_overlap(self):
        import os
        import pathlib
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.check_call(["git", "init"], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pathlib.Path(os.path.join(tmp, "LICENSE")).write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3\n")
            pathlib.Path(os.path.join(tmp, "NEW-WORK.md")).write_text("work\n")
            result = subprocess.run(["python3", str(_guard_path)], cwd=tmp, capture_output=True, text=True)
            combined = result.stdout + result.stderr
            self.assertNotIn("old_repos not configured", combined)

    def test_allowed_url_is_ignored(self):
        allowed = [f'https://github.com/{HANDLE}/old-phone-os']
        text = f'git clone {allowed[0]}'
        self.assertIsNone(PAT.search(mod.strip_allowed(text, allowed)))

    def test_other_handle_use_still_matches(self):
        allowed = [f'https://github.com/{HANDLE}/old-phone-os']
        for txt in [f'by {HANDLE}', f'https://github.com/{HANDLE}', f'https://github.com/{HANDLE}/old-phone-os-evil']:
            self.assertIsNotNone(PAT.search(mod.strip_allowed(txt, allowed)))

    def test_allowed_text_ignores_bad_entries(self):
        self.assertEqual(mod.allowed_text({}), [])
        self.assertEqual(mod.allowed_text({'allow_text': [1, '', 'x']}), ['x'])


if __name__ == "__main__":
    unittest.main()
