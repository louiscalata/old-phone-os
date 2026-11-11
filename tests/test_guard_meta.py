# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import unittest
import subprocess
import os
import pathlib
import sys
import tempfile

spec = importlib.util.spec_from_file_location('check', 'guard/check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

class TestGuardMeta(unittest.TestCase):
    def test_source_archive_outside_git_reports_not_run(self):
        guard_path = pathlib.Path(__file__).resolve().parents[1] / 'guard' / 'check.py'
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(pathlib.Path(tmp).resolve().parent))
            result = subprocess.run([sys.executable, '-B', str(guard_path)], cwd=tmp,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, 'guard: NOT RUN (run from the root of an OPOS Git checkout; source archives lack Git metadata)\n')
            self.assertEqual(result.stderr, '')

    def test_source_archive_does_not_inspect_parent_checkout(self):
        guard_path = pathlib.Path(__file__).resolve().parents[1] / 'guard' / 'check.py'
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.check_call(['git', 'init'], cwd=tmp,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Even invalid parent configuration must not be read before the root check.
            pathlib.Path(tmp, '.git', 'guard.json').write_text('not JSON')
            archive = pathlib.Path(tmp, 'downloaded-archive')
            archive.mkdir()
            for args in ([], ['--staged']):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, '-B', str(guard_path), *args],
                                            cwd=archive, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, 'guard: NOT RUN (run from the root of an OPOS Git checkout; source archives lack Git metadata)\n')
                    self.assertEqual(result.stderr, '')

    def test_deploy_ai_tools(self):
        text = '"Claude Code"\n"https://claude.ai/install.sh"'
        errs = check.meta_only_errors('deploy/ai-tools.json', text)
        self.assertTrue(any('non-Meta model reference' in e for e in errs))
        self.assertTrue(any('non-Meta host claude.ai' in e for e in errs))

    def test_codex_chatgpt(self):
        text = 'codex\nhttps://chatgpt.com/codex/install.sh'
        errs = check.meta_only_errors('src/x.py', text)
        self.assertTrue(any('non-Meta model reference' in e for e in errs))
        self.assertTrue(any('non-Meta host chatgpt.com' in e for e in errs))

    def test_meta_allowed(self):
        text = 'Muse Code (Meta)\nhttps://dev.meta.ai/install.sh'
        errs = check.meta_only_errors('src/y.py', text)
        self.assertEqual(errs, [])
        text2 = 'http://127.0.0.1:8765'
        errs2 = check.meta_only_errors('src/z.py', text2)
        self.assertEqual(errs2, [])

    def test_gpt4o_scope(self):
        line = 'MODEL = "gpt-4o"'
        errs_src = check.meta_only_errors('src/a.py', line)
        self.assertEqual(len(errs_src), 1)
        errs_docs = check.meta_only_errors('docs/x.md', line)
        self.assertEqual(errs_docs, [])

    def test_search_host_allowed(self):
        ok = check.meta_only_errors('src/a.py', 'https://en.wikipedia.org/w/api.php')
        self.assertEqual(ok, [])
        bad = check.meta_only_errors('src/a.py', 'https://example.com/x')
        self.assertEqual(len(bad), 1)
        self.assertTrue(any('example.com' in e for e in bad))

    def test_url_credential_bypass(self):
        evil = check.meta_only_errors('src/app.py', 'https://meta.ai@evil.example/x')
        self.assertIn('src/app.py: non-Meta host evil.example', evil)
        evil2 = check.meta_only_errors('src/app.py', 'https://evil.example/@meta.ai')
        self.assertIn('src/app.py: non-Meta host evil.example', evil2)
        self.assertEqual(check.meta_only_errors('src/app.py', 'https://api.meta.ai/v1'), [])
        self.assertEqual(check.meta_only_errors('src/app.py', 'http://127.0.0.1:8080/'), [])
        cred_url = ''.join(['https://','u',':','p','@','api','.','meta','.','ai','/'])
        errs_cred = check.meta_only_errors('src/app.py', cred_url)
        self.assertTrue(len(errs_cred) > 0)

    def test_public_landing_navigation_exception_is_exact(self):
        path = 'src/opos/public_static/public-demo.html'
        for url in ('https://github.com/louiscalata/old-phone-os',
                    'https://fortheloveof.studio/code/old-phone-os/'):
            anchor = '<a href="' + url + '">Project</a>'
            self.assertEqual(check.meta_only_errors(path, anchor), [])
            self.assertTrue(check.meta_only_errors('src/other.html', anchor))
            self.assertTrue(check.meta_only_errors(path, 'fetch("' + url + '")'))
            self.assertTrue(check.meta_only_errors(path, '<script src="' + url + '"></script>'))
            self.assertTrue(check.meta_only_errors(path, '<a href="' + url + '/other">Elsewhere</a>'))
        self.assertTrue(check.meta_only_errors(path, 'claude'))

    def test_reviewed_font_notices_are_exact_non_runtime_text(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        for name in ('INTERTIGHT-OFL.txt', 'NEWSREADER-OFL.txt'):
            path = 'src/opos/web_static/fonts/' + name
            text = (root / path).read_text(encoding='utf-8')
            with self.subTest(name=name):
                self.assertEqual(check.meta_only_errors(path, text), [])
                # Text changes and executable additions require a fresh review.
                self.assertTrue(check.meta_only_errors(path, text + 'changed'))
                self.assertTrue(check.meta_only_errors(path, text + '\nfetch("https://github.com/x")'))
                self.assertTrue(check.meta_only_errors(path, text + '\n<script src="https://scripts.sil.org/x"></script>'))
                self.assertTrue(check.meta_only_errors(path, text + '\nclaude'))
                # The same URLs never gain a general host or runtime-code exception.
                self.assertTrue(check.meta_only_errors('src/app.py', text))
                self.assertTrue(check.meta_only_errors(path, 'fetch("https://github.com/x")'))
                self.assertTrue(check.meta_only_errors('src/opos/web_static/fonts/OTHER-OFL.txt', text))

    def test_repo_files_clean(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if not os.path.exists(os.path.join(root, '.git')):
            self.skipTest("not a git checkout (git archive copy)")
        out = subprocess.check_output(['git', 'ls-files', 'src', 'deploy', 'install.sh', 'install.ps1', 'requirements.txt'], cwd=root, text=True)
        for f in out.splitlines():
            if not f:
                continue
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                    txt = fh.read()
            except OSError:
                continue
            errs = check.meta_only_errors(f, txt)
            self.assertEqual(errs, [], f'Unexpected errors in {f}')

    def test_no_guard_json_skips_overlap(self):
        import pathlib
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.check_call(["git", "init"], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pathlib.Path(tmp, "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3\n")
            pathlib.Path(tmp, "NEW-WORK.md").write_text("work\n")
            guard_path = pathlib.Path(__file__).resolve().parents[1] / "guard" / "check.py"
            result = subprocess.run(["python3", str(guard_path)], cwd=tmp, capture_output=True, text=True)
            combined = result.stdout + result.stderr
            self.assertNotIn("old_repos not configured", combined)

    def test_spdx_header_enforcement(self):
        import pathlib
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.check_call(["git", "init"], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pathlib.Path(tmp, "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3\n")
            pathlib.Path(tmp, "NEW-WORK.md").write_text("work\n")
            guard_path = pathlib.Path(__file__).resolve().parents[1] / "guard" / "check.py"
            header = "# SPDX-License-Identifier: GPL-3.0-or-later\n"
            shebang = "#!/usr/bin/env python3\n"
            comment = "# harmless comment\n"
            paths = ["sample.py", "sample.sh", "sample.mjs", "sample.ps1", ".githooks/pre-commit", "src/opos/setup/__init__.py", "tests/__init__.py"]
            for path in paths:
                with self.subTest(path=path):
                    target = pathlib.Path(tmp, path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fail_contents = ["", comment, comment * 3 + header]
                    pass_contents = [header, shebang + header, shebang + comment + header]
                    for content in fail_contents:
                        with self.subTest(content=content):
                            target.write_text(content)
                            result = subprocess.run(["python3", str(guard_path)], cwd=tmp, capture_output=True, text=True)
                            self.assertEqual(result.returncode, 1)
                            self.assertIn(f"GUARD FAIL: {path}: missing SPDX header", result.stdout.splitlines())
                            target.unlink()
                    for content in pass_contents:
                        with self.subTest(content=content):
                            target.write_text(content)
                            result = subprocess.run(["python3", str(guard_path)], cwd=tmp, capture_output=True, text=True)
                            self.assertEqual(result.returncode, 0)
                            target.unlink()
            target = pathlib.Path(tmp, "deploy/ai-tools.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}")
            result = subprocess.run(["python3", str(guard_path)], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)

    def test_f2053_flagged_files_have_spdx_header(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        header = "# SPDX-License-Identifier: GPL-3.0-or-later"
        js_header = "// SPDX-License-Identifier: GPL-3.0-or-later"
        paths = (
            ".githooks/commit-msg",
            ".githooks/pre-commit",
            ".githooks/pre-push",
            "src/opos/__init__.py",
            "src/opos/aitools/__init__.py",
            "src/opos/setup/__init__.py",
            "tests/__init__.py",
            "tests/test_setup_server.py",
            "tests/test_setup_wizard_page.py",
            "tests/ui_check.mjs",
        )
        for path in paths:
            with self.subTest(path=path):
                target = root / path
                self.assertTrue(target.is_file(), f"{path} does not exist")
                lines = target.read_text(encoding="utf-8").splitlines()[:3]
                self.assertTrue(
                    any(
                        line.startswith(header)
                        or (path.endswith(".mjs") and line.startswith(js_header))
                        for line in lines
                    ),
                    f"{path}: missing SPDX header in first 3 lines",
                )
                if path.startswith(".githooks/"):
                    self.assertTrue(lines, f"{path} is empty")
                    self.assertTrue(
                        lines[0].startswith("#!"),
                        f"{path}: first line does not start with shebang",
                    )

    def test_spdx_header_check_accepts_javascript_line_comment(self):
        import pathlib
        original_read_text = pathlib.Path.read_text
        def fake_read_text(self, *args, **kwargs):
            if str(self).endswith("tests/ui_check.mjs"):
                return "// SPDX-License-Identifier: GPL-3.0-or-later\n"
            return original_read_text(self, *args, **kwargs)
        pathlib.Path.read_text = fake_read_text
        try:
            self.test_f2053_flagged_files_have_spdx_header()
        finally:
            pathlib.Path.read_text = original_read_text

if __name__ == '__main__':
    unittest.main()
