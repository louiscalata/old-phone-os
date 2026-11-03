# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from tests.pmos_lint import apkbuild_vars

ARCHIVE = "fixture-release-source.tar.gz"
SOURCE_ROOT = "fixture-reviewed-source"
REMOTE_DIGEST = "a" * 128
SCRIPT = Path(__file__).resolve().parent.parent / "packaging/pmos/stage-aports.sh"


class TestStageLocalSource(unittest.TestCase):
    def _make_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        repo, out = base / "repo", base / "out"
        for package in ("opos", "postmarketos-ui-opos"):
            (repo / "packaging/pmos" / package).mkdir(parents=True)
        (repo / "deploy").mkdir()
        for name, data in (
            ("opos-sysusers.conf", b"fixture users\n"),
        ):
            (repo / "deploy" / name).write_bytes(data)
        (repo / "src/opos").mkdir(parents=True)
        (repo / "src/opos/__init__.py").write_bytes(b"fixture source bytes\n")
        (repo / "LICENSE").write_bytes(b"GPL version 3\nFixture license bytes.\n")
        (repo / "THIRD_PARTY.md").write_bytes(b"Fixture notices: no model weights.\n")
        auxiliary = [
            (
                hashlib.sha512((repo / "deploy" / name).read_bytes()).hexdigest(),
                name,
            )
            for name in ("opos-sysusers.conf",)
        ]
        checksum_text = "\n".join(
            f"{digest}  {name}" for digest, name in auxiliary
        )
        recipe = (
            'pkgname=opos\npkgver=1.2.3\n'
            f'_source_archive="{ARCHIVE}"\n'
            f'_source_root="{SOURCE_ROOT}"\n'
            'source="$_source_archive::https://example.invalid/$_source_archive '
            'opos-sysusers.conf"\n'
            f'sha512sums="\n{REMOTE_DIGEST}  $_source_archive\n'
            f'{checksum_text}\n"\n'
        )
        (repo / "packaging/pmos/opos/APKBUILD").write_text(recipe)
        (repo / "packaging/pmos/postmarketos-ui-opos/APKBUILD").write_text(
            'pkgname="postmarketos-ui-opos"\npkgver=1\n',
        )
        script = repo / "packaging/pmos/stage-aports.sh"
        shutil.copyfile(SCRIPT, script)
        helper = SCRIPT.parent / "make-runtime-source.py"
        shutil.copyfile(helper, script.parent / helper.name)
        (script.parent / "runtime-files.txt").write_text("src/opos/__init__.py\n")
        return repo, out, script

    def _run(self, repo, out, script, local=True):
        env = dict(os.environ, COPYFILE_DISABLE="1")
        env.pop("OPOS_LOCAL_SOURCE", None)
        if local:
            env["OPOS_LOCAL_SOURCE"] = "1"
        return subprocess.run(
            ["sh", str(script), str(out)], cwd=repo, env=env,
            capture_output=True, text=True, timeout=20, check=False,
        )

    def _assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def _snapshot(self, repo):
        return {
            path.relative_to(repo).as_posix(): (
                path.read_bytes(), stat.S_IMODE(path.stat().st_mode),
            )
            for path in repo.rglob("*") if path.is_file()
        }

    def _checksums(self, recipe):
        tokens = apkbuild_vars(recipe)["sha512sums"].split()
        self.assertEqual(len(tokens) % 2, 0)
        return dict(zip(tokens[1::2], tokens[::2]))

    def test_tarball_uses_literal_archive_name_and_root(self):
        repo, out, script = self._make_repo()
        self._assert_success(self._run(repo, out, script))
        self.assertFalse((out / "main/opos/opos-1.2.3.tar.gz").exists())
        with tarfile.open(out / "main/opos" / ARCHIVE, "r:gz") as archive:
            members = archive.getmembers()
            self.assertTrue(members)
            self.assertTrue(
                all(
                    member.name == SOURCE_ROOT
                    or member.name.startswith(SOURCE_ROOT + "/")
                    for member in members
                ),
            )
            self.assertIsNotNone(
                archive.getmember(SOURCE_ROOT + "/src/opos/__init__.py"),
            )

    def test_tarball_preserves_license_notices_and_runtime_bytes(self):
        repo, out, script = self._make_repo()
        runtime = b"# fixture runtime\n__version__ = 'unchanged'\n"
        (repo / "src/opos/__init__.py").write_bytes(runtime)
        self._assert_success(self._run(repo, out, script))
        with tarfile.open(out / "main/opos" / ARCHIVE, "r:gz") as archive:
            for relative in ("LICENSE", "THIRD_PARTY.md", "src/opos/__init__.py"):
                with self.subTest(path=relative):
                    member = archive.getmember(SOURCE_ROOT + "/" + relative)
                    self.assertTrue(member.isfile())
                    self.assertEqual(
                        archive.extractfile(member).read(),
                        (repo / relative).read_bytes(),
                    )

    def test_local_checksum_matches_local_tar_and_preserves_auxiliary_digests(self):
        repo, out, script = self._make_repo()
        caller = repo / "packaging/pmos/opos/APKBUILD"
        before = self._checksums(caller)
        self._assert_success(self._run(repo, out, script))
        staged = out / "main/opos/APKBUILD"
        after = self._checksums(staged)
        actual = hashlib.sha512((out / "main/opos" / ARCHIVE).read_bytes()).hexdigest()
        self.assertEqual(after["$_source_archive"], actual)
        self.assertNotEqual(actual, REMOTE_DIGEST)
        self.assertEqual(set(before), set(after))
        for name in before.keys() - {"$_source_archive"}:
            with self.subTest(source=name):
                self.assertEqual(after[name], before[name])

    def test_only_staged_archive_url_is_removed_and_caller_is_unchanged(self):
        repo, out, script = self._make_repo()
        before = self._snapshot(repo)
        self._assert_success(self._run(repo, out, script))
        self.assertEqual(self._snapshot(repo), before)
        self.assertEqual(
            apkbuild_vars(out / "main/opos/APKBUILD")["source"],
            "$_source_archive opos-sysusers.conf",
        )
        self.assertIn(
            "::https://example.invalid/",
            apkbuild_vars(repo / "packaging/pmos/opos/APKBUILD")["source"],
        )
        self.assertFalse(list(out.glob(".srcpack.*")))

    def test_bytecode_is_not_packed(self):
        repo, out, script = self._make_repo()
        cache = repo / "src/opos/__pycache__"
        cache.mkdir()
        (cache / "module.pyc").write_bytes(b"not source")
        (repo / "src/opos/old.pyo").write_bytes(b"not source")
        self._assert_success(self._run(repo, out, script))
        with tarfile.open(out / "main/opos" / ARCHIVE, "r:gz") as archive:
            self.assertFalse(
                any(
                    "__pycache__" in member.name
                    or member.name.endswith((".pyc", ".pyo"))
                    for member in archive.getmembers()
                ),
            )
        self.assertTrue((cache / "module.pyc").exists())

    def test_default_mode_copies_even_minimal_recipe_without_local_validation(self):
        repo, out, script = self._make_repo()
        caller = repo / "packaging/pmos/opos/APKBUILD"
        caller.write_text("minimal default-stage fixture\n")
        self._assert_success(self._run(repo, out, script, local=False))
        self.assertEqual((out / "main/opos/APKBUILD").read_bytes(), caller.read_bytes())
        self.assertFalse(list(out.rglob("*.tar.gz")))

    def test_unsafe_or_ambiguous_metadata_is_rejected_without_execution(self):
        cases = (
            ("_source_archive", '"../escape.tar.gz"'),
            ("_source_archive", "ABSOLUTE"),
            ("_source_archive", '"-bad.tar.gz"'),
            ("_source_archive", '"wrong-extension.zip"'),
            ("_source_archive", '"duplicate.tar.gz"\n_source_archive="other.tar.gz"'),
            ("_source_root", '"../escape"'),
            ("_source_root", "ABSOLUTE"),
            ("_source_root", '".."'),
            ("_source_root", "unquoted-name"),
            ("_source_root", '"duplicate"\n_source_root="other"'),
            ("_source_root", None),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                repo, out, script = self._make_repo()
                marker = repo.parent / "executed"
                if value is None:
                    value = f'"$(touch "{marker}")"'
                elif value == "ABSOLUTE":
                    name = "absolute.tar.gz" if key == "_source_archive" else "absolute-root"
                    value = f'"{repo.parent / name}"'
                recipe = repo / "packaging/pmos/opos/APKBUILD"
                lines = recipe.read_text().splitlines()
                lines = [f"{key}={value}" if line.startswith(key + "=") else line for line in lines]
                recipe.write_text("\n".join(lines) + "\n")
                before = self._snapshot(repo)
                result = self._run(repo, out, script)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())
                self.assertEqual(self._snapshot(repo), before)
                self.assertFalse((repo.parent / "escape.tar.gz").exists())
                self.assertFalse((repo.parent / "absolute.tar.gz").exists())
                self.assertFalse((repo.parent / "absolute-root").exists())
                self.assertFalse((out / "main/escape.tar.gz").exists())
                self.assertFalse((out / "escape").exists())
                self.assertFalse(list(out.glob(".srcpack.*")))

    def test_missing_license_or_notice_stops_local_staging(self):
        for missing in ("LICENSE", "THIRD_PARTY.md"):
            with self.subTest(missing=missing):
                repo, out, script = self._make_repo()
                (repo / missing).unlink()
                result = self._run(repo, out, script)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((out / "main/opos" / ARCHIVE).exists())
                self.assertFalse(list(out.glob(".srcpack.*")))

    def test_indented_export_and_readonly_duplicates_are_rejected(self):
        for key, value in (
            ("_source_archive", "../escape.tar.gz"),
            ("_source_root", "../escape"),
        ):
            for prefix in ("  ", "\t", "export ", "readonly "):
                with self.subTest(key=key, prefix=prefix):
                    repo, out, script = self._make_repo()
                    recipe = repo / "packaging/pmos/opos/APKBUILD"
                    recipe.write_text(
                        recipe.read_text() + f'{prefix}{key}="{value}"\n',
                    )
                    before = self._snapshot(repo)
                    result = self._run(repo, out, script)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self._snapshot(repo), before)
                    self.assertFalse((out / "main/escape.tar.gz").exists())
                    self.assertFalse((out / "escape").exists())
                    self.assertFalse(list(out.glob(".srcpack.*")))

    def test_untracked_source_and_metadata_do_not_enter_archive(self):
        repo, out, script = self._make_repo()
        (repo / "src/opos/untracked.py").write_text("not part of the declared release inputs\n")
        (repo / "src/opos/._metadata").write_bytes(b"not source")
        self._assert_success(self._run(repo, out, script))
        with tarfile.open(out / "main/opos" / ARCHIVE, "r:gz") as archive:
            names = archive.getnames()
            self.assertFalse(any("untracked" in name or "._metadata" in name for name in names))
