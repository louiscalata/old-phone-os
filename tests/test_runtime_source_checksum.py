# SPDX-License-Identifier: GPL-3.0-or-later
"""Release checksums follow the supplied artifact, not the current source tree."""
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging/pmos/update-runtime-checksum.py"
spec = importlib.util.spec_from_file_location("runtime_source_checksum", SCRIPT)
checksum = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checksum)


class RuntimeSourceChecksum(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.recipe = self.base / "APKBUILD"
        self.original = (ROOT / "packaging/pmos/opos/APKBUILD").read_text(encoding="utf-8")
        self.recipe.write_text(self.original, encoding="utf-8")
        name = self.original.split('_source_archive="', 1)[1].split('"', 1)[0]
        self.archive = self.base / name
        self.archive.write_bytes(b"explicit release artifact bytes\n")
        self.digest = hashlib.sha512(self.archive.read_bytes()).hexdigest()

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.archive),
             "--apkbuild", str(self.recipe), *args],
            capture_output=True, text=True,
        )

    def test_refresh_changes_only_release_archive_checksum(self):
        before, after = self.original.split("  $_source_archive", 1)
        prefix, old_digest = before.rsplit("\n", 1)
        self.assertNotEqual(old_digest, self.digest)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.digest + "  " + self.archive.name + "\n")
        expected = prefix + "\n" + self.digest + "  $_source_archive" + after
        self.assertEqual(self.recipe.read_text(encoding="utf-8"), expected)
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recipe.read_text(encoding="utf-8"), expected)

    def test_check_reports_mismatch_without_writing(self):
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("checksum does not match", result.stderr)
        self.assertEqual(self.recipe.read_text(encoding="utf-8"), self.original)

    def test_check_detects_changed_artifact_without_rewriting_recipe(self):
        checksum.update_checksum(self.recipe, self.archive)
        before = self.recipe.read_bytes()
        self.archive.write_bytes(b"different artifact bytes\n")
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("checksum does not match", result.stderr)
        self.assertEqual(self.recipe.read_bytes(), before)

    def test_missing_or_wrong_named_artifact_does_not_change_recipe(self):
        for archive in (self.base / "other.tar.gz", self.archive):
            with self.subTest(archive=archive):
                self.archive.unlink(missing_ok=True)
                with self.assertRaises((ValueError, OSError)):
                    checksum.update_checksum(self.recipe, archive)
                self.assertEqual(self.recipe.read_text(encoding="utf-8"), self.original)

    def test_invalid_metadata_is_rejected_without_writing_or_execution(self):
        archive_line = '_source_archive="' + self.archive.name + '"'
        marker = self.base / "executed"
        before = self.original.split("  $_source_archive", 1)[0]
        digest = before.rsplit("\n", 1)[1]
        checksum_line = digest + "  $_source_archive"
        for text in (
            self.original.replace(archive_line, ""),
            self.original + "\n readonly " + archive_line + "\n",
            self.original.replace(archive_line, '_source_archive="../escape.tar.gz"'),
            self.original.replace(archive_line, '_source_archive="$(touch ' + str(marker) + ')"'),
            self.original.replace(checksum_line, ""),
            self.original.replace(checksum_line, checksum_line + "\n" + checksum_line),
            self.original.replace(checksum_line, "SKIP  $_source_archive"),
            self.original.replace('sha512sums="', 'sha512sums=""'),
            self.original + '\nexport sha512sums=""\n',
        ):
            with self.subTest(text=text):
                self.recipe.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    checksum.update_checksum(self.recipe, self.archive)
                self.assertEqual(self.recipe.read_text(encoding="utf-8"), text)
                self.assertFalse(marker.exists())
