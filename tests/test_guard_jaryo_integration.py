# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import os
import re
import hashlib
import unittest
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from contextlib import redirect_stdout
import io

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
spec = importlib.util.spec_from_file_location('check', os.path.join(ROOT, 'guard', 'check.py'))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

class TestGuardJaryoIntegration(unittest.TestCase):
    def test_jry_id_positive(self):
        good = ['uiux-JRY00', 'uiux-JRY99', 'uiux-JRY12-fixup', 'uiux-JRY12-fix3', 'uiux-JRY12-fix10']
        for pid in good:
            self.assertTrue(check.JRY_ID.fullmatch(pid), pid)

    def test_jry_id_negative(self):
        bad = ['uiux-JRY1', 'uiux-JRY100', 'uiux-JRY12-evil', 'uiux-JRY12-fix', 'uiux-JRY12-fix123',
               'uiux-JRY12-fixup-fixup', 'uiux-JRY12-', 'uiux-JRYab', 'xuiux-JRY12']
        for pid in bad:
            self.assertFalse(check.JRY_ID.fullmatch(pid), pid)

    def test_jry_model_positive(self):
        good = ['written by Opus 5.5', 'written by Sol 6.1.', 'written by Fable', 'Astra via the executor']
        for s in good:
            self.assertTrue(check.JRY_MODEL.search(s), s)

    def test_jry_model_negative(self):
        bad = ['Opus 5.5.1', 'Sol 6.15', 'Fabled', 'written by a model', 'Opus 5.55']
        for s in bad:
            self.assertFalse(check.JRY_MODEL.search(s), s)

    def test_jry_ok_same_row(self):
        pid = 'uiux-JRY12-fixup'
        lines = [f'{pid} written by Opus 5.5']
        self.assertTrue(check.jry_ok(pid, lines))

    def test_jry_model_on_another_row_rejected(self):
        self.assertFalse(check.jry_ok('uiux-JRY12', [
            'uiux-JRY12 written by an unknown model',
            'uiux-JRY13 written by Opus 5.5',
        ]))

    def test_jry_ok_wrong_id_or_model(self):
        pid = 'uiux-JRY12-fixup'
        lines = [f'uiux-JRY13 written by Opus 5.5']
        self.assertFalse(check.jry_ok(pid, lines))
        lines2 = [f'{pid} written by Opus 5.5.1']
        self.assertFalse(check.jry_ok(pid, lines2))

    def test_jry_ok_shorter_id_vs_longer_row_rejection(self):
        pid_short = 'uiux-JRY12'
        pid_long = 'uiux-JRY12-fixup'
        lines = [f'{pid_long} written by Opus 5.5']
        self.assertFalse(check.jry_ok(pid_short, lines))
        self.assertTrue(check.jry_ok(pid_long, lines))

    def test_uiux_model_fable2_rejection(self):
        self.assertFalse(bool(check.UIUX_MODEL.search('written by Fable2')))
        self.assertFalse(bool(check.UIUX_MODEL.search('Fable v2')))
        self.assertTrue(bool(check.UIUX_MODEL.search('written by Fable')))

    def test_canonical_helpers_unchanged(self):
        self.assertTrue(check.UIUX_ID.fullmatch('uiux-UX01'))
        self.assertTrue(check.UIUX_PHONE_ID.match('uiux-UXP20'))
        self.assertTrue(check.UIUX_PHONE_ID.match('uiux-UXP20-r1a-fix3'))
        self.assertFalse(check.UIUX_PHONE_ID.match('uiux-UXP100'))
        self.assertFalse(check.UIUX_PHONE_ID.match('uiux-UXP20-evil'))
        self.assertFalse(check.UIUX_PHONE_ID.match('uiux-UXP20/F9999'))
        self.assertFalse(check.UIUX_PHONE_ID.match('uiux-UX01'))
        self.assertFalse(check.UIUX_PHONE_ID.match('uiux-JRY00'))
        self.assertFalse(check.UIUX_ID.match('uiux-JRY00'))
        self.assertTrue(check.META.search('written by muse-glimmer-30b'))
        self.assertFalse(check.META.search('written by Fable'))

    def test_uiux_phone_id_absolute_end(self):
        pid_ok = 'uiux-UXP20'
        pid_ok2 = 'uiux-UXP20-r1a'
        self.assertTrue(bool(check.UIUX_PHONE_ID.match(pid_ok)))
        self.assertTrue(bool(check.UIUX_PHONE_ID.match(pid_ok2)))
        self.assertFalse(bool(check.UIUX_PHONE_ID.match(pid_ok + '\n')))
        self.assertFalse(bool(check.UIUX_PHONE_ID.match(pid_ok + ' appended')))
        self.assertFalse(bool(check.UIUX_PHONE_ID.match('uiux-UXP20-r1a' + 'bad')))
        self.assertFalse(bool(check.UIUX_PHONE_ID.match('uiux-UXP20-r1a\n')))

    def test_md5_usedforsecurity_false_semantics(self):
        s = 'test line for md5 normalization'
        norm = re.sub(r'\s+', ' ', s).strip()
        expected = hashlib.md5(norm.encode(), usedforsecurity=False).hexdigest()
        nl = check.norm_lines('  ' + s + '  \nshort\n' + s)
        self.assertIn(expected, nl)
        self.assertEqual(len(nl), 1)
        long1 = 'this is a sufficiently long normalized line one for testing'
        long2 = 'another sufficiently long normalized line two for testing'
        short = 'short'
        fixture = f'  {long1}  \n{short}\n{long2}\n  {long1}\n'
        expected_set = {
            hashlib.md5(long1.encode(), usedforsecurity=False).hexdigest(),
            hashlib.md5(long2.encode(), usedforsecurity=False).hexdigest(),
        }
        self.assertEqual(check.norm_lines(fixture), expected_set)

    def test_main_dispatch_jaryo_table(self):
        cases = [
            ('uiux-fable', 'uiux-JRY12-fixup', 'uiux-JRY12-fixup written by Opus 5.5', 0),
            ('integration/opos-20261101', 'uiux-JRY12-fixup', 'uiux-JRY12-fixup written by Opus 5.5', 0),
            ('meta-forward', 'uiux-JRY12-fixup', 'uiux-JRY12-fixup written by Opus 5.5', 0),
            ('unknown-branch', 'uiux-JRY12-fixup', 'uiux-JRY12-fixup written by Opus 5.5', 0),
            ('main', 'uiux-JRY12-fixup', 'uiux-JRY12-fixup written by Opus 5.5', 0),
            ('HEAD', 'uiux-JRY12-fixup', 'uiux-JRY12-fixup written by Opus 5.5', 0),
            ('main', 'uiux-UXF01', 'uiux-UXF01 written by Fable', 0),
            ('main', 'fix-F3013a', 'fix-F3013a written by Sol 6.1', 0),
            ('main', 'uiux-OTHER', 'uiux-OTHER written by Fable', 1),
            ('main', 'uiux-JRY12', 'uiux-JRY12-fixup written by Opus 5.5', 1),
        ]
        def make_isfile(path):
            cfg_path = os.path.join('.git', 'guard.json')
            if path == cfg_path:
                return False
            if path == 'NEW-WORK.md':
                return True
            return False
        for branch, pid, prov, expected_rc in cases:
            with self.subTest(branch=branch, pid=pid):
                commit = '0'*40 + ' 1793100000 1793100000 ' + pid + '\x1e'
                def fake_git(*a, **k):
                    key = a
                    if key == ('rev-parse', '--git-common-dir'):
                        return '.git\n'
                    if key[:2] == ('log', '--format=%H %at %ct %(trailers:key=Provenance,valueonly,separator=%x20)%x1e'):
                        return commit
                    if key == ('rev-parse', '--abbrev-ref', 'HEAD'):
                        return branch + '\n'
                    return ''
                def fake_read(p):
                    if p == 'LICENSE':
                        return 'GNU GENERAL PUBLIC LICENSE\nVersion 3\n'
                    if p == 'docs/provenance.md':
                        return prov
                    if p in ('guard/deps.json', '.git/guard.json'):
                        return '{}'
                    return ''
                with patch.object(check, 'sys', create=True) as mock_sys:
                    mock_sys.argv = ['check.py']
                    with patch.object(check, 'git', side_effect=fake_git):
                        with patch.object(check, 'read', side_effect=fake_read):
                            with patch.object(check, 'files', return_value=[]):
                                with patch('os.path.isfile', side_effect=make_isfile):
                                    rc = check.main()
                                    self.assertEqual(rc, expected_rc)


class TestGuardJaryoNativeCheckout(unittest.TestCase):
    """Exercise the real guard against Git history, without checkout-name mocks."""

    def make_repo(self, pid='uiux-JRY12-fixup', prov=None,
                  author_date=1793100000, commit_date=1793100000, runtime=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        # Keep fixture commits independent of user hooks and signing settings.
        self.git(repo, 'init', '-q', '-b', 'main')
        self.git(repo, 'config', 'user.name', 'Guard Fixture')
        self.git(repo, 'config', 'user.email', 'fixture@example.com')
        (repo / 'docs').mkdir()
        (repo / 'guard').mkdir()
        (repo / 'LICENSE').write_text('GNU GENERAL PUBLIC LICENSE\nVersion 3\n')
        (repo / 'NEW-WORK.md').write_text('Synthetic guard fixture.\n')
        (repo / 'guard/deps.json').write_text('{}\n')
        if prov is None:
            prov = 'uiux-JRY12-fixup written by Opus 5.5'
        (repo / 'docs/provenance.md').write_text(prov + '\n')
        if runtime is not None:
            (repo / 'src').mkdir()
            (repo / 'src/fixture.py').write_text(runtime)
        self.git(repo, 'add', '.')
        env = dict(os.environ, GIT_AUTHOR_DATE=f'{author_date} +0000',
                   GIT_COMMITTER_DATE=f'{commit_date} +0000')
        args = ['commit', '-q', '-m', 'Synthetic guard fixture']
        if pid:
            args += ['-m', 'Provenance: ' + pid]
        self.git(repo, *args, env=env)
        return repo

    def git(self, repo, *args, env=None):
        return subprocess.run(
            ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false', *args],
            cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()

    def guard(self, repo, *args):
        return subprocess.run([sys.executable, '-B', str(Path(ROOT) / 'guard/check.py'), *args],
                              cwd=repo, capture_output=True, text=True)

    def test_same_history_passes_on_named_and_detached_checkouts(self):
        repo = self.make_repo()
        head = self.git(repo, 'rev-parse', 'HEAD')
        for branch in ('main', 'meta-forward', 'HEAD'):
            with self.subTest(branch=branch):
                if branch == 'meta-forward':
                    self.git(repo, 'checkout', '-q', '-b', branch)
                elif branch == 'HEAD':
                    self.git(repo, 'checkout', '-q', '--detach', head)
                self.assertEqual(self.git(repo, 'rev-parse', 'HEAD'), head)
                self.assertEqual(self.git(repo, 'rev-parse', '--abbrev-ref', 'HEAD'), branch)
                for args in ((), ('--staged',)):
                    proc = self.guard(repo, *args)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn('guard: PASS', proc.stdout)

    def test_checkout_portability_preserves_rejections(self):
        provenance_error = 'no Provenance trailer logged'
        cases = [
            ({'pid': 'uiux-JRY12-evil', 'prov': 'uiux-JRY12-evil written by Opus 5.5'}, provenance_error),
            ({'prov': 'uiux-JRY12-fixup written by Opus 5.5.1'}, provenance_error),
            ({'prov': 'uiux-JRY12-fixup written by an unknown model\nuiux-JRY13 written by Opus 5.5'}, provenance_error),
            ({'pid': 'uiux-JRY12'}, provenance_error),
            ({'pid': 'unrelated-task', 'prov': 'unrelated-task written by Astra'}, provenance_error),
            ({'pid': ''}, provenance_error),
            ({'author_date': check.KICKOFF - 1}, 'dated before kickoff'),
            ({'commit_date': check.DEADLINE + 1}, 'after deadline'),
            ({'runtime': '# SPDX-License-Identifier: GPL-3.0-or-later\n# Claude runtime reference\n'},
             'src/fixture.py:2: non-Meta model reference'),
        ]
        for kwargs, error in cases:
            repo = self.make_repo(**kwargs)
            for branch in ('main', 'HEAD'):
                with self.subTest(case=kwargs, branch=branch):
                    if branch == 'HEAD':
                        self.git(repo, 'checkout', '-q', '--detach')
                    proc = self.guard(repo)
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn(error, proc.stdout)

if __name__ == '__main__':
    unittest.main()
