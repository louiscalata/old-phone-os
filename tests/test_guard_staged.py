# SPDX-License-Identifier: GPL-3.0-or-later;
import unittest
import os
import tempfile
import subprocess

class TestGuardStagedFiles(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        subprocess.run(['git', 'init', '-q'], check=True)
        git_dir = subprocess.check_output(['git', 'rev-parse', '--git-common-dir'], text=True).strip()
        with open(os.path.join(git_dir, 'guard.json'), 'w', encoding='utf-8') as fh:
            fh.write('{}')
        import guard.check as check
        self.check = check

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self._tmp.cleanup()

    def test_staged_preserves_spaces_nonascii_newline(self):
        names = [
            'normal.txt',
            'space name.txt',
            'café.txt',
            'newline\nname.txt',
        ]
        for name in names:
            with open(name, 'w', encoding='utf-8') as fh:
                fh.write('ok')
        subprocess.run(['git', 'add', '.'], check=True)
        files = self.check.files(True)
        for name in names:
            self.assertIn(name, files, f'staged file {name!r} missing')
        self.assertEqual(len(files), len(names))
        self.assertNotIn('does-not-exist.txt', files)

    def test_unstaged_preserves_spaces_nonascii(self):
        names = [
            'untracked space.txt',
            'ünicode.txt',
        ]
        for name in names:
            with open(name, 'w', encoding='utf-8') as fh:
                fh.write('ok')
        files = self.check.files(False)
        for name in names:
            self.assertIn(name, files, f'untracked file {name!r} missing')
            self.assertTrue(os.path.isfile(name))

    def test_staged_checks_use_index_snapshot(self):
        import sys
        check_path = os.path.abspath(self.check.__file__)
        py = sys.executable
        def staged_run():
            return subprocess.run([py, check_path, '--staged'], capture_output=True, text=True)
        def fresh():
            prev = os.getcwd()
            td = tempfile.TemporaryDirectory()
            os.chdir(td.name)
            subprocess.run(['git', 'init', '-q'], check=True)
            subprocess.run(['git', 'config', 'user.email', 'fixture@example.com'], check=True)
            subprocess.run(['git', 'config', 'user.name', 'Fixture'], check=True)
            os.makedirs('guard', exist_ok=True)
            os.makedirs('docs', exist_ok=True)
            with open('LICENSE', 'w', encoding='utf-8') as fh:
                fh.write('GNU GENERAL PUBLIC LICENSE\nVersion 3\n')
            with open('NEW-WORK.md', 'w', encoding='utf-8') as fh:
                fh.write('baseline\n')
            with open('guard/deps.json', 'w', encoding='utf-8') as fh:
                fh.write('{}')
            with open('docs/provenance.md', 'w', encoding='utf-8') as fh:
                fh.write('fixture-baseline Muse\n')
            subprocess.run(['git', 'add', 'LICENSE', 'NEW-WORK.md', 'guard/deps.json', 'docs/provenance.md'], check=True)
            env = dict(os.environ, GIT_AUTHOR_DATE='2026-10-28T12:00:00+00:00', GIT_COMMITTER_DATE='2026-10-28T12:00:00+00:00')
            subprocess.run(['git', '-c', 'commit.gpgsign=false', 'commit', '--no-verify', '-q', '-m', 'baseline', '-m', 'Provenance: fixture-baseline'], check=True, env=env)
            proc = staged_run()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return td, prev
        def done(td, prev):
            os.chdir(prev)
            td.cleanup()
        bad = ''.join(['/', 'Users/', 'Fixture'])
        td, prev = fresh()
        try:
            with open('sample.txt', 'w', encoding='utf-8') as fh:
                fh.write('x ' + bad + '\n')
            subprocess.run(['git', 'add', 'sample.txt'], check=True)
            with open('sample.txt', 'w', encoding='utf-8') as fh:
                fh.write('clean\n')
            indexed = subprocess.check_output(['git', 'show', ':sample.txt'], text=True)
            self.assertIn(bad, indexed)
            with open('sample.txt', encoding='utf-8') as fh:
                self.assertNotIn(bad, fh.read())
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('sample.txt: private/secret match', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            with open('sample.txt', 'w', encoding='utf-8') as fh:
                fh.write('x ' + bad + '\n')
            subprocess.run(['git', 'add', 'sample.txt'], check=True)
            os.remove('sample.txt')
            indexed = subprocess.check_output(['git', 'show', ':sample.txt'], text=True)
            self.assertIn(bad, indexed)
            self.assertFalse(os.path.exists('sample.txt'))
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('sample.txt: private/secret match', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            with open('sample.txt', 'w', encoding='utf-8') as fh:
                fh.write('clean\n')
            subprocess.run(['git', 'add', 'sample.txt'], check=True)
            with open('sample.txt', 'w', encoding='utf-8') as fh:
                fh.write('x ' + bad + '\n')
            indexed = subprocess.check_output(['git', 'show', ':sample.txt'], text=True)
            self.assertNotIn(bad, indexed)
            with open('sample.txt', encoding='utf-8') as fh:
                self.assertIn(bad, fh.read())
            proc = staged_run()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('guard: PASS', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            with open('payload.img', 'w', encoding='utf-8') as fh:
                fh.write('clean\n')
            subprocess.run(['git', 'add', 'payload.img'], check=True)
            os.remove('payload.img')
            out = subprocess.check_output(['git', 'ls-files', '--stage'], text=True)
            self.assertIn('payload.img', out)
            self.assertFalse(os.path.exists('payload.img'))
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('payload.img: device image or vendor blob', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            big = self.check.MAX_BYTES + 1
            with open('large.dat', 'wb') as fh:
                fh.write(b'a' * big)
            subprocess.run(['git', 'add', 'large.dat'], check=True)
            with open('large.dat', 'wb') as fh:
                fh.write(b'a')
            size = int(subprocess.check_output(['git', 'cat-file', '-s', ':large.dat'], text=True).strip())
            self.assertEqual(size, big)
            self.assertTrue(os.path.getsize('large.dat') < big)
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('large.dat: larger than 5 MB', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            exact = self.check.MAX_BYTES
            with open('large.dat', 'wb') as fh:
                fh.write(b'a' * exact)
            subprocess.run(['git', 'add', 'large.dat'], check=True)
            with open('large.dat', 'wb') as fh:
                fh.write(b'a')
            size = int(subprocess.check_output(['git', 'cat-file', '-s', ':large.dat'], text=True).strip())
            self.assertEqual(size, exact)
            proc = staged_run()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('guard: PASS', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            with open('requirements.txt', 'w', encoding='utf-8') as fh:
                fh.write('fixturedep==1.0\n')
            subprocess.run(['git', 'add', 'requirements.txt'], check=True)
            with open('requirements.txt', 'w', encoding='utf-8') as fh:
                fh.write('')
            indexed = subprocess.check_output(['git', 'show', ':requirements.txt'], text=True)
            self.assertIn('fixturedep', indexed)
            with open('requirements.txt', encoding='utf-8') as fh:
                self.assertNotIn('fixturedep', fh.read())
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('dependency fixturedep: license unlisted in guard/deps.json', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            with open('requirements.txt', 'w', encoding='utf-8') as fh:
                fh.write('fixturedep==1.0\n')
            subprocess.run(['git', 'add', 'requirements.txt'], check=True)
            with open('guard/deps.json', 'w', encoding='utf-8') as fh:
                fh.write('{"fixturedep": "MIT"}')
            indexed = subprocess.check_output(['git', 'show', ':guard/deps.json'], text=True)
            self.assertEqual(indexed.strip(), '{}')
            with open('guard/deps.json', encoding='utf-8') as fh:
                self.assertIn('MIT', fh.read())
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('dependency fixturedep: license unlisted in guard/deps.json', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            with open('requirements.txt', 'w', encoding='utf-8') as fh:
                fh.write('fixturedep==1.0\n')
            with open('guard/deps.json', 'w', encoding='utf-8') as fh:
                fh.write('{"fixturedep": "MIT"}')
            subprocess.run(['git', 'add', 'requirements.txt', 'guard/deps.json'], check=True)
            with open('guard/deps.json', 'w', encoding='utf-8') as fh:
                fh.write('{}')
            indexed = subprocess.check_output(['git', 'show', ':guard/deps.json'], text=True)
            self.assertIn('MIT', indexed)
            with open('guard/deps.json', encoding='utf-8') as fh:
                self.assertNotIn('MIT', fh.read())
            proc = staged_run()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('guard: PASS', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            with open('LICENSE', 'w', encoding='utf-8') as fh:
                fh.write('invalid\n')
            subprocess.run(['git', 'add', 'LICENSE'], check=True)
            with open('LICENSE', 'w', encoding='utf-8') as fh:
                fh.write('GNU GENERAL PUBLIC LICENSE\nVersion 3\n')
            indexed = subprocess.check_output(['git', 'show', ':LICENSE'], text=True)
            self.assertNotIn('GNU GENERAL PUBLIC LICENSE', indexed)
            with open('LICENSE', encoding='utf-8') as fh:
                self.assertIn('GNU GENERAL PUBLIC LICENSE', fh.read())
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('LICENSE: missing or not GPL-3.0', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            subprocess.run(['git', 'rm', '--cached', '-q', 'NEW-WORK.md'], check=True)
            self.assertTrue(os.path.isfile('NEW-WORK.md'))
            out = subprocess.check_output(['git', 'ls-files'], text=True)
            self.assertNotIn('NEW-WORK.md', out)
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('NEW-WORK.md: missing', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            with open('docs/provenance.md', 'w', encoding='utf-8') as fh:
                fh.write('clean Muse\n')
            subprocess.run(['git', 'add', 'docs/provenance.md'], check=True)
            with open('docs/provenance.md', 'w', encoding='utf-8') as fh:
                fh.write('fixture-baseline Muse\n')
            indexed = subprocess.check_output(['git', 'show', ':docs/provenance.md'], text=True)
            self.assertNotIn('fixture-baseline', indexed)
            with open('docs/provenance.md', encoding='utf-8') as fh:
                self.assertIn('fixture-baseline', fh.read())
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('no Provenance trailer logged with a Meta model', proc.stdout)
        finally:
            done(td, prev)
        td, prev = fresh()
        try:
            target = ''.join(['/', 'Users/', 'Fixture'])
            os.symlink(target, 'link.txt')
            subprocess.run(['git', 'add', 'link.txt'], check=True)
            mode = subprocess.check_output(['git', 'ls-files', '--stage'], text=True)
            self.assertIn('120000', mode)
            self.assertIn('link.txt', mode)
            indexed = subprocess.check_output(['git', 'show', ':link.txt'], text=True)
            self.assertIn(target, indexed)
            os.remove('link.txt')
            with open('link.txt', 'w', encoding='utf-8') as fh:
                fh.write('clean\n')
            self.assertTrue(os.path.isfile('link.txt'))
            self.assertFalse(os.path.islink('link.txt'))
            proc = staged_run()
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn('link.txt: private/secret match', proc.stdout)
        finally:
            done(td, prev)

    def test_unstaged_inspection_fails_closed(self):
        import contextlib
        import io
        import stat as statmod
        orig_git = self.check.git
        orig_stat = self.check.os.stat
        def make_msg(tag):
            sec = ''.join(['s', 'k']) + chr(45) + ''.join(['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P', 'A', 'S', 'D', 'F', 'G', 'H', 'J', 'K']) + tag
            abs_path = chr(47) + ''.join(['t', 'm', 'p']) + chr(47) + ''.join(['g', 'u', 'a', 'r', 'd']) + chr(47) + tag
            return sec + ' ' + abs_path, sec, abs_path
        try:
            for tag, exc in [('A1', FileNotFoundError), ('B2', PermissionError)]:
                with self.subTest(exc=exc):
                    msg, sec, abs_path = make_msg(tag)
                    self.check.git = lambda *a: 'audit.txt\x00'
                    def do_stat(path, _msg=msg, _exc=exc):
                        raise _exc(_msg)
                    self.check.os.stat = do_stat
                    out = io.StringIO()
                    err = io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        with self.assertRaises(SystemExit) as cm:
                            self.check.files(False)
                    self.assertEqual(cm.exception.code, 1)
                    self.assertEqual(err.getvalue(), 'GUARD FAIL: audit input unavailable: audit.txt\n')
                    self.assertEqual(out.getvalue(), '')
                    combined = out.getvalue() + err.getvalue()
                    self.assertNotIn(sec, combined)
                    self.assertNotIn(abs_path, combined)
            with self.subTest(kind='regular'):
                self.check.git = lambda *a: 'audit.txt\x00'
                holder = type('St', (), {})()
                holder.st_mode = statmod.S_IFREG | 0o644
                self.check.os.stat = lambda p: holder
                self.assertEqual(self.check.files(False), ['audit.txt'])
            with self.subTest(kind='directory'):
                self.check.git = lambda *a: 'audit.txt\x00'
                holder = type('St', (), {})()
                holder.st_mode = statmod.S_IFDIR | 0o755
                self.check.os.stat = lambda p: holder
                self.assertEqual(self.check.files(False), [])
            with self.subTest(kind='empty'):
                self.check.git = lambda *a: ''
                def no_stat(path):
                    raise AssertionError('stat must not be called')
                self.check.os.stat = no_stat
                self.assertEqual(self.check.files(False), [])
            with self.subTest(kind='staged'):
                self.check.git = lambda *a: 'audit.txt\x00'
                def no_stat_staged(path):
                    raise AssertionError('stat must not be called')
                self.check.os.stat = no_stat_staged
                self.assertEqual(self.check.files(True), ['audit.txt'])
        finally:
            self.check.git = orig_git
            self.check.os.stat = orig_stat

if __name__ == '__main__':
    unittest.main()
