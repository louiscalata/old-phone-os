# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import collections
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from opos.aitools import catalog
from opos.aitools import place

def _tool(archs: tuple[str, ...] = ("aarch64", "x86_64"), min_free_mb: int = 10) -> catalog.Tool:
    """Build minimal Tool for tests."""
    return catalog.Tool(id="test-tool", name="Test Tool", binary="testbin", installer_url="https://example.com/install.sh", interpreter="sh", installer_args=(), installer_env={}, archs=archs, min_free_mb=min_free_mb, state_subdir="state", run_env={}, lite_env={}, signin="do signin", docs_url="https://example.com/docs")

class TestNormalizeArch(unittest.TestCase):
    """Cover normalize_arch."""
    def test_aarch64_variants(self):
        """Map aarch64 forms."""
        self.assertEqual(place.normalize_arch("aarch64"), "aarch64")
        self.assertEqual(place.normalize_arch("AARCH64"), "aarch64")
        self.assertEqual(place.normalize_arch("arm64"), "aarch64")
        self.assertEqual(place.normalize_arch("ARM64"), "aarch64")
        self.assertEqual(place.normalize_arch("Arm64"), "aarch64")

    def test_x86_64_variants(self):
        """Map x86_64 forms."""
        self.assertEqual(place.normalize_arch("x86_64"), "x86_64")
        self.assertEqual(place.normalize_arch("X86_64"), "x86_64")
        self.assertEqual(place.normalize_arch("amd64"), "x86_64")
        self.assertEqual(place.normalize_arch("AMD64"), "x86_64")
        self.assertEqual(place.normalize_arch("Amd64"), "x86_64")

    def test_unknown_returns_none(self):
        """Unknown arches give None."""
        self.assertIsNone(place.normalize_arch("armv7l"))
        self.assertIsNone(place.normalize_arch("mips"))
        self.assertIsNone(place.normalize_arch(""))
        self.assertIsNone(place.normalize_arch(" "))
        self.assertIsNone(place.normalize_arch("x86"))
        self.assertIsNone(place.normalize_arch("aarch64\n"))

class TestChoosePrefix(unittest.TestCase):
    """Cover choose_prefix."""
    @unittest.skipIf(sys.platform == "win32", "POSIX absolute paths not on Windows")
    def test_env_absolute_override(self):
        """Absolute env wins."""
        home = Path("/fake/home")
        env = {"OPOS_TOOLS_PREFIX": "/home/u/custom"}
        self.assertEqual(place.choose_prefix(env, home, persistent_roots=("/nonexistent",)), Path("/home/u/custom"))

    @unittest.skipIf(sys.platform == "win32", "POSIX absolute paths not on Windows")
    def test_env_absolute_wins_over_persistent(self):
        """Env overrides writable root."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "userdata"
            root.mkdir()
            home = Path(td) / "home"
            env = {"OPOS_TOOLS_PREFIX": "/home/u/override"}
            got = place.choose_prefix(env, home, persistent_roots=(str(root),))
            self.assertEqual(got, Path("/home/u/override"))

    def test_env_relative_ignored_uses_persistent(self):
        """Relative env is ignored."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "userdata"
            root.mkdir()
            home = Path(td) / "home"
            env = {"OPOS_TOOLS_PREFIX": "relative/path"}
            got = place.choose_prefix(env, home, persistent_roots=(str(root),))
            self.assertEqual(got, root / "opos")

    def test_env_relative_ignored_fallback(self):
        """Relative env falls back to home."""
        home = Path("/fake/home")
        env = {"OPOS_TOOLS_PREFIX": "relative/path"}
        got = place.choose_prefix(env, home, persistent_roots=("/nonexistent-a", "/nonexistent-b"))
        self.assertEqual(got, home / ".local" / "share" / "opos")

    def test_env_empty_ignored(self):
        """Empty env ignored."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "userdata"
            root.mkdir()
            home = Path(td) / "home"
            env = {"OPOS_TOOLS_PREFIX": ""}
            got = place.choose_prefix(env, home, persistent_roots=(str(root),))
            self.assertEqual(got, root / "opos")

    def test_writable_persistent_root(self):
        """First writable root chosen."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "userdata"
            root.mkdir()
            home = Path(td) / "home"
            got = place.choose_prefix({}, home, persistent_roots=(str(root),))
            self.assertEqual(got, root / "opos")

    def test_persistent_first_of_many(self):
        """First writable of many wins."""
        with tempfile.TemporaryDirectory() as td:
            r1 = Path(td) / "r1"
            r2 = Path(td) / "r2"
            r1.mkdir()
            r2.mkdir()
            home = Path(td) / "home"
            got = place.choose_prefix({}, home, persistent_roots=(str(r1), str(r2)))
            self.assertEqual(got, r1 / "opos")

    def test_persistent_non_dir_ignored(self):
        """File persistent root ignored."""
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "file"
            f.write_text("x", encoding="utf-8")
            home = Path(td) / "home"
            got = place.choose_prefix({}, home, persistent_roots=(str(f),))
            self.assertEqual(got, home / ".local" / "share" / "opos")

    def test_persistent_missing_fallback(self):
        """Missing roots fallback to home."""
        home = Path("/fake/home")
        got = place.choose_prefix({}, home, persistent_roots=("/nonexistent-xyz-123",))
        self.assertEqual(got, home / ".local" / "share" / "opos")

    def test_home_fallback_structure(self):
        """Fallback path is home/.local/share/opos."""
        home = Path("/fake/home")
        got = place.choose_prefix({}, home, persistent_roots=())
        self.assertEqual(got, Path("/fake/home/.local/share/opos"))

class TestFreeMb(unittest.TestCase):
    """Cover free_mb."""
    def test_nonexistent_uses_parent(self):
        """Nonexistent path uses nearest parent."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            nested = base / "a" / "b" / "c"
            Du = collections.namedtuple("du", "total used free")
            fixed = Du(100, 40, 287 * 1024 * 1024)
            with mock.patch.object(place.shutil, "disk_usage", return_value=fixed) as m:
                got = place.free_mb(nested)
            self.assertEqual(got, fixed.free // (1024 * 1024))
            self.assertEqual(Path(m.call_args[0][0]), base)

    def test_nonexistent_deep_with_existing_mid(self):
        """Mid parent existent is used."""
        with tempfile.TemporaryDirectory() as td:
            mid = Path(td) / "a"
            mid.mkdir()
            nested = mid / "b" / "c" / "d"
            Du = collections.namedtuple("du", "total used free")
            fixed = Du(100, 40, 287 * 1024 * 1024)
            with mock.patch.object(place.shutil, "disk_usage", return_value=fixed) as m:
                got = place.free_mb(nested)
            self.assertEqual(got, fixed.free // (1024 * 1024))
            self.assertEqual(Path(m.call_args[0][0]), mid)

    def test_existing_path(self):
        """Existing path returns int."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "file.txt"
            p.write_text("hi", encoding="utf-8")
            Du = collections.namedtuple("du", "total used free")
            fixed = Du(100, 40, 287 * 1024 * 1024)
            with mock.patch.object(place.shutil, "disk_usage", return_value=fixed) as m:
                got = place.free_mb(p)
            self.assertEqual(got, fixed.free // (1024 * 1024))
            self.assertIsInstance(got, int)
            self.assertEqual(Path(m.call_args[0][0]), p)

    def test_existing_dir(self):
        """Existing dir matches disk_usage."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            Du = collections.namedtuple("du", "total used free")
            fixed = Du(100, 40, 287 * 1024 * 1024)
            with mock.patch.object(place.shutil, "disk_usage", return_value=fixed) as m:
                got = place.free_mb(base)
            self.assertEqual(got, fixed.free // (1024 * 1024))
            self.assertEqual(Path(m.call_args[0][0]), base)

@unittest.skipIf(sys.platform == "win32", "/proc/mounts parsing is POSIX-only")
class TestVolatileWarning(unittest.TestCase):
    """Cover volatile_warning."""
    def _write(self, td: str, content: str) -> Path:
        """Write mounts file."""
        p = Path(td) / "mounts"
        p.write_text(content, encoding="utf-8")
        return p

    def test_tmpfs_returns_warning(self):
        """Tmpfs prefix warns."""
        with tempfile.TemporaryDirectory() as td:
            m = self._write(td, "tmpfs /tmp tmpfs rw 0 0\n")
            w = place.volatile_warning(Path("/tmp/opos"), m)
            self.assertIsNotNone(w)
            self.assertIn("tmpfs", w)
            self.assertIn("/tmp/opos", w)
            self.assertIn("lost on reboot", w)

    def test_ext4_no_warning(self):
        """Ext4 gives no warning."""
        with tempfile.TemporaryDirectory() as td:
            m = self._write(td, "/dev/sda1 / ext4 rw 0 0\n")
            self.assertIsNone(place.volatile_warning(Path("/home/u/tool"), m))

    def test_overlay_variants_warn(self):
        """Overlay family warns."""
        for fstype in ("overlay", "overlayfs", "ramfs", "tmpfs"):
            with tempfile.TemporaryDirectory() as td:
                m = self._write(td, f"none /volatile {fstype} rw 0 0\n")
                w = place.volatile_warning(Path("/volatile/tool"), m)
                self.assertIsNotNone(w)
                self.assertIn(fstype, w)

    def test_nested_longest_match_ext4_wins(self):
        """Longest mount wins."""
        with tempfile.TemporaryDirectory() as td:
            content = "/dev/sda1 / ext4 rw 0 0\nnone /volatile tmpfs rw 0 0\n/dev/sda2 /volatile/sub ext4 rw 0 0\n"
            m = self._write(td, content)
            self.assertIsNone(place.volatile_warning(Path("/volatile/sub/tool"), m))
            w = place.volatile_warning(Path("/volatile/other"), m)
            self.assertIsNotNone(w)
            self.assertIn("tmpfs", w)

    def test_nested_longest_match_tmpfs_wins(self):
        """Nested tmpfs warns."""
        with tempfile.TemporaryDirectory() as td:
            content = "/dev/sda1 / ext4 rw 0 0\n/dev/sda1 /a ext4 rw 0 0\ntmpfs /a/b tmpfs rw 0 0\n"
            m = self._write(td, content)
            self.assertIsNone(place.volatile_warning(Path("/a/tool"), m))
            self.assertIsNotNone(place.volatile_warning(Path("/a/b/tool"), m))

    def test_root_tmpfs_warns(self):
        """Root tmpfs warns for subprefix."""
        with tempfile.TemporaryDirectory() as td:
            m = self._write(td, "tmpfs / tmpfs rw 0 0\n")
            w = place.volatile_warning(Path("/home/u/tool"), m)
            self.assertIsNotNone(w)
            self.assertIn("tmpfs", w)

    def test_root_ext4_no_warn(self):
        """Root ext4 no warning."""
        with tempfile.TemporaryDirectory() as td:
            m = self._write(td, "/dev/root / ext4 rw 0 0\n")
            self.assertIsNone(place.volatile_warning(Path("/home/u/tool"), m))

    def test_missing_file_none(self):
        """Missing mounts file is None."""
        self.assertIsNone(place.volatile_warning(Path("/home/u/tool"), Path("/nonexistent/mounts/file")))

    def test_malformed_lines_ignored(self):
        """Short lines ignored."""
        with tempfile.TemporaryDirectory() as td:
            m = self._write(td, "badline\n\n/dev/sda1 / ext4 rw 0 0\n")
            self.assertIsNone(place.volatile_warning(Path("/home/u/tool"), m))

    def test_escaped_space(self):
        """\\040 decoded."""
        with tempfile.TemporaryDirectory() as td:
            m = self._write(td, "/dev/sda1 /mnt/fake\\040space ext4 rw 0 0\ntmpfs /mnt/fake\\040space/tmp tmpfs rw 0 0\n")
            self.assertIsNone(place.volatile_warning(Path("/mnt/fake space/tool"), m))
            self.assertIsNotNone(place.volatile_warning(Path("/mnt/fake space/tmp/tool"), m))

    def test_exact_mount_match(self):
        """Exact mount equality matches."""
        with tempfile.TemporaryDirectory() as td:
            m = self._write(td, "tmpfs /tmp tmpfs rw 0 0\n")
            w = place.volatile_warning(Path("/tmp"), m)
            self.assertIsNotNone(w)

class TestPreflight(unittest.TestCase):
    """Cover preflight."""
    def test_bad_arch(self):
        """Unsupported arch reported."""
        t = _tool(archs=("aarch64",), min_free_mb=1)
        probs = place.preflight(t, Path("/fake/prefix"), "x86_64", free_mb_fn=lambda p: 1000)
        self.assertEqual(len(probs), 1)
        self.assertIn("x86_64", probs[0])
        self.assertIn("unsupported", probs[0].lower())

    def test_unknown_arch(self):
        """Unknown machine reported."""
        t = _tool(archs=("x86_64",), min_free_mb=1)
        probs = place.preflight(t, Path("/fake/prefix"), "mips", free_mb_fn=lambda p: 1000)
        self.assertTrue(any("mips" in s for s in probs))

    def test_low_space(self):
        """Low space reported."""
        t = _tool(archs=("x86_64",), min_free_mb=100)
        probs = place.preflight(t, Path("/fake/prefix"), "x86_64", free_mb_fn=lambda p: 5)
        self.assertEqual(len(probs), 1)
        self.assertIn("only 5 MB free", probs[0])
        self.assertIn("100 MB required", probs[0])

    def test_ok(self):
        """Supported arch and space ok."""
        t = _tool(archs=("x86_64",), min_free_mb=10)
        probs = place.preflight(t, Path("/fake/prefix"), "x86_64", free_mb_fn=lambda p: 1000)
        self.assertEqual(probs, [])

    def test_ok_with_both_archs(self):
        """Either arch ok."""
        t = _tool(archs=("aarch64", "x86_64"), min_free_mb=10)
        self.assertEqual(place.preflight(t, Path("/fake/prefix"), "aarch64", free_mb_fn=lambda p: 100), [])
        self.assertEqual(place.preflight(t, Path("/fake/prefix"), "x86_64", free_mb_fn=lambda p: 100), [])

    def test_both_problems(self):
        """Arch and space both reported."""
        t = _tool(archs=("aarch64",), min_free_mb=100)
        probs = place.preflight(t, Path("/fake/prefix"), "x86_64", free_mb_fn=lambda p: 1)
        self.assertEqual(len(probs), 2)
        self.assertTrue(any("x86_64" in s for s in probs))
        self.assertTrue(any("only 1 MB free" in s for s in probs))

    def test_case_insensitive_arch(self):
        """Arch check case-insensitive."""
        t = _tool(archs=("x86_64",), min_free_mb=1)
        self.assertEqual(place.preflight(t, Path("/fake/prefix"), "AMD64", free_mb_fn=lambda p: 100), [])
        t2 = _tool(archs=("aarch64",), min_free_mb=1)
        self.assertEqual(place.preflight(t2, Path("/fake/prefix"), "ARM64", free_mb_fn=lambda p: 100), [])

    def test_exact_free_ok(self):
        """Exact free equals required is ok."""
        t = _tool(archs=("x86_64",), min_free_mb=50)
        probs = place.preflight(t, Path("/fake/prefix"), "x86_64", free_mb_fn=lambda p: 50)
        self.assertEqual(probs, [])

    def test_free_one_less_fails(self):
        """One MB less fails."""
        t = _tool(archs=("x86_64",), min_free_mb=50)
        probs = place.preflight(t, Path("/fake/prefix"), "x86_64", free_mb_fn=lambda p: 49)
        self.assertEqual(len(probs), 1)
        self.assertIn("49", probs[0])

    def test_arm64_maps_to_aarch64(self):
        """Arm64 machine satisfies aarch64 tool."""
        t = _tool(archs=("aarch64",), min_free_mb=1)
        self.assertEqual(place.preflight(t, Path("/fake/prefix"), "arm64", free_mb_fn=lambda p: 100), [])

    def test_amd64_maps_to_x86_64(self):
        """Amd64 machine satisfies x86_64 tool."""
        t = _tool(archs=("x86_64",), min_free_mb=1)
        self.assertEqual(place.preflight(t, Path("/fake/prefix"), "amd64", free_mb_fn=lambda p: 100), [])
