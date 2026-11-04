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

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
spec = importlib.util.spec_from_file_location('check', os.path.join(ROOT, 'guard', 'check.py'))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

class TestGuardProvenance(unittest.TestCase):
    def test_meta_rule_unchanged(self):
        self.assertTrue(check.META.search('written by muse-glimmer-30b'))
        for model in ('Sol 6.1', 'Fable', 'Astra'):
            self.assertFalse(check.META.search('written by ' + model))

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

class TestGuardProvenanceNativeCheckout(unittest.TestCase):
    """Exercise the real guard against Git history, without checkout-name mocks."""

    def make_repo(self, pid='fixture-task-fixup', prov=None,
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
            prov = 'fixture-task-fixup written by Muse Glimmer'
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
            ({'prov': 'fixture-task-fixup written by an unknown model\nfixture-other written by Muse Glimmer'}, provenance_error),
            ({'pid': 'fixture-task'}, provenance_error),
            ({'pid': 'unrelated-task', 'prov': 'unrelated-task written by Astra'}, provenance_error),
            # Former private-history exceptions must now obey the same rule.
            ({'pid': 'uiux-JRY12-fixup', 'prov': 'uiux-JRY12-fixup written by Opus 5.5'}, provenance_error),
            ({'pid': 'uiux-UX01', 'prov': 'uiux-UX01 written by Astra'}, provenance_error),
            ({'pid': 'uiux-UXP20', 'prov': 'uiux-UXP20 written by Fable'}, provenance_error),
            ({'pid': 'uiux-UXF01', 'prov': 'uiux-UXF01 written by Fable'}, provenance_error),
            ({'pid': 'fix-F3013a', 'prov': 'fix-F3013a written by Sol 6.1'}, provenance_error),
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
