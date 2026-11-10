# SPDX-License-Identifier: GPL-3.0-or-later
"""Declared-source admission and normalized package-input archive checks."""
import hashlib
import importlib.util
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path

from tests.pmos_lint import apkbuild_vars

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging/pmos/make-runtime-source.py"
spec = importlib.util.spec_from_file_location("runtime_source_builder", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class RuntimeSourceArchive(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "repo"
        (self.root / "src/opos").mkdir(parents=True)
        (self.root / "packaging/pmos").mkdir(parents=True)
        (self.root / "src/opos/a.py").write_bytes(b"editable Python source\n")
        (self.root / "src/opos/b.js").write_bytes(b"editable JavaScript source\n")
        (self.root / "src/opos/b.js").chmod(0o755)
        (self.root / "LICENSE").write_bytes(b"fixture GPL text\n")
        (self.root / "THIRD_PARTY.md").write_bytes(b"fixture notices\n")
        self.inventory = self.root / "packaging/pmos/runtime-files.txt"
        self.inventory.write_text("src/opos/a.py\nsrc/opos/b.js\n")
        self.output = self.base / "output/runtime.tar.gz"

    def build(self):
        return builder.create_archive(self.root, self.output, "old-phone-os-source")

    def test_declared_bytes_modes_and_license_inputs_are_present(self):
        self.build()
        with tarfile.open(self.output, "r:gz") as archive:
            files = [m for m in archive.getmembers() if m.isfile()]
            self.assertEqual(len(files), 4)
            for member in files:
                relative = member.name.removeprefix("old-phone-os-source/")
                self.assertEqual(archive.extractfile(member).read(),
                                 (self.root / relative).read_bytes())
                expected_mode = 0o755 if relative.endswith("b.js") else 0o644
                self.assertEqual(member.mode, expected_mode)
            for member in archive.getmembers():
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
                self.assertEqual((member.uname, member.gname), ("", ""))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o644)

    def test_rebuild_ignores_source_mtime_and_undeclared_files(self):
        self.build()
        before = self.output.read_bytes()
        for path in self.root.rglob("*"):
            os.utime(path, (123, 123))
        (self.root / "src/opos/untracked.py").write_text("not a release input")
        cache = self.root / "src/opos/__pycache__"
        cache.mkdir()
        (cache / "a.pyc").write_bytes(b"not editable source")
        self.build()
        self.assertEqual(self.output.read_bytes(), before)
        with tarfile.open(self.output, "r:gz") as archive:
            self.assertFalse(any("untracked" in n or "__pycache__" in n
                                 for n in archive.getnames()))

    def test_unsafe_duplicate_unsorted_and_bytecode_inventory_is_rejected(self):
        for text in (
            "src/opos/../escape\n", "src/opos/./a.py\n",
            "src//opos/a.py\n", " src/opos/a.py\n", "/src/opos/a.py\n",
            "src/opos/a.py\nsrc/opos/a.py\n", "src/opos/b.js\nsrc/opos/a.py\n",
            "src/opos/a.pyc\n", "src/opos/__pycache__/a.py\n",
            "src/opos/._metadata\n", "",
        ):
            with self.subTest(text=text):
                self.inventory.write_text(text)
                with self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())

    def test_symlinked_input_and_parent_are_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "a.py").write_text("outside source")
        original = self.root / "src/opos/a.py"
        original.unlink()
        original.symlink_to(outside / "a.py")
        with self.assertRaises(ValueError):
            self.build()
        original.unlink()
        (self.root / "src/opos/b.js").unlink()
        (self.root / "src/opos").rmdir()
        (self.root / "src/opos").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_license_or_notice_does_not_replace_an_existing_archive(self):
        self.build()
        before = self.output.read_bytes()
        (self.root / "THIRD_PARTY.md").unlink()
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.assertEqual(self.output.read_bytes(), before)
        self.assertFalse(list(self.output.parent.glob(".opos-runtime-*")))

    def test_output_cannot_replace_inventory_or_another_source_tree_file(self):
        before = self.inventory.read_bytes()
        with self.assertRaises(ValueError):
            builder.create_archive(self.root, self.inventory, "old-phone-os-source")
        self.assertEqual(self.inventory.read_bytes(), before)
        with self.assertRaises(ValueError):
            builder.create_archive(self.root, self.root / "new.tar.gz", "old-phone-os-source")

    def test_release_recipe_digest_matches_current_declared_runtime_bytes(self):
        values = apkbuild_vars(ROOT / "packaging/pmos/opos/APKBUILD")
        output = self.base / values["_source_archive"]
        paths = builder.create_archive(ROOT, output, values["_source_root"])
        self.assertEqual(len(paths), 132)  # 118 declared runtime files, LICENSE and THIRD_PARTY.md
        tokens = values["sha512sums"].split()
        self.assertEqual(tokens[1], "$_source_archive")
        self.assertEqual(tokens[0], hashlib.sha512(output.read_bytes()).hexdigest())
        declared = set(paths) - {"LICENSE", "THIRD_PARTY.md"}
        actual = {
            p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
            and p.suffix not in (".pyc", ".pyo") and not p.name.startswith("._")
        }
        self.assertEqual(declared, actual)
