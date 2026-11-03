# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise bootstrap against a local source fixture, with no network/service work."""
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_installer import fixture_git_env, fresh_sandbox

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bootstrap.sh"


@unittest.skipUnless(shutil.which("git"), "git unavailable")
class BootstrapReferences(unittest.TestCase):
    def setUp(self):
        temporary = fresh_sandbox()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / "fixture"
        self.repo.mkdir()
        self.env = fixture_git_env(self.repo)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "OPOS test fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "install.sh").write_text(
            '#!/bin/sh\n'
            'd="$(cd "$(dirname "$0")" && pwd -P)"\n'
            'printf "%s\\n" "$*" > "$OPOS_TEST_INSTALL_MARKER"\n'
            'cat "$d/version.txt" >> "$OPOS_TEST_INSTALL_MARKER"\n'
        )
        (self.repo / "version.txt").write_text("one\n")
        self.git("add", "install.sh", "version.txt")
        self.git("commit", "-m", "fixture one")
        self.first = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("tag", "fixture-tag")
        (self.repo / "version.txt").write_text("two\n")
        self.git("add", "version.txt")
        self.git("commit", "-m", "fixture two")
        self.marker = self.base / "installed"
        self.dest = self.base / "checkout"
        self.env.update(
            HOME=str(self.base), OPOS_REPO_URL=str(self.repo),
            OPOS_SRC_DIR=str(self.dest), OPOS_TEST_INSTALL_MARKER=str(self.marker),
        )

    def git(self, *args):
        return subprocess.run(
            [shutil.which("git"), "-C", str(self.repo), *args],
            env=self.env, capture_output=True, text=True, check=True, timeout=20,
        )

    def run_bootstrap(self, ref):
        env = dict(self.env, OPOS_REF=ref)
        return subprocess.run(["sh", str(SCRIPT)], check=False, env=env,
                              capture_output=True, text=True, timeout=20)

    def test_full_commit_is_checked_out_before_install(self):
        result = self.run_bootstrap(self.first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_text().splitlines(), ["--yes --no-launch", "one"])
        head = subprocess.check_output(
            [shutil.which("git"), "-C", str(self.dest), "rev-parse", "HEAD"], env=self.env,
        ).decode().strip()
        self.assertEqual(head, self.first)

    def test_tag_and_branch_references_still_work(self):
        for reference, expected in (("fixture-tag", "one"), ("main", "two")):
            with self.subTest(reference=reference):
                self.env["OPOS_SRC_DIR"] = str(self.base / reference)
                result = self.run_bootstrap(reference)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.marker.read_text().splitlines()[-1], expected)

    def test_invalid_reference_fails_before_clone_or_install(self):
        result = self.run_bootstrap("--upload-pack=unexpected")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.marker.exists())

    def test_unreachable_commit_does_not_run_installer(self):
        result = self.run_bootstrap("f" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_repository_option_is_not_executed_as_a_git_option(self):
        sentinel = self.base / "unexpected"
        self.env["OPOS_REPO_URL"] = "--upload-pack=touch " + str(sentinel)
        result = self.run_bootstrap("main")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(sentinel.exists())
        self.assertFalse(self.marker.exists())
