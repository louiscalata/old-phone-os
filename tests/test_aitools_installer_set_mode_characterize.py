# SPDX-License-Identifier: GPL-3.0-or-later
"""Characterization tests for installer.set_mode() and installer.uninstall().

These pin exact messages, check order, return values, rollback text and exception propagation as they stood
before both functions were split into helpers (LOU-84), so the split can be checked against them.
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opos.aitools import installer, launcher
from opos.aitools.catalog import Tool

SRC = Path("/fake/src")
MISSING_TAIL = (
    " (wrapper left unchanged; restore this tool's original catalog entry, including installer_url, "
    "then rerun the mode command)"
)


def _tool(tid: str, binary: str) -> Tool:
    return Tool(
        id=tid,
        name=tid,
        binary=binary,
        installer_url=f"https://example.com/{tid}.sh",
        interpreter="sh",
        installer_args=(),
        installer_env={},
        archs=("x86_64",),
        min_free_mb=1,
        state_subdir="state",
        run_env={},
        lite_env={},
        signin=f"run {binary} login",
        docs_url="https://example.com/docs",
        installer_sha256="0" * 64,
    )


TA = _tool("tool-a", "bina")
TB = _tool("tool-b", "binb")
TC = _tool("tool-c", "binc")


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.prefix = self.base / "prefix"
        self.tr = installer.tools_root(self.prefix)
        self.mp = installer.manifest_path(self.prefix)
        self.bin = self.tr / "bin"
        self.run_dir = self.tr / "run"

    def write_manifest(self, data) -> None:
        self.mp.parent.mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data)
        self.mp.write_text(text, encoding="utf-8")

    def manifest(self) -> dict:
        return json.loads(self.mp.read_text(encoding="utf-8"))

    def sb(self, tid: str) -> Path:
        return self.tr / tid / "home"


class SetModeCharacterization(_Base):
    def two_tool_manifest(self, **extra) -> dict:
        m = {
            "version": 1,
            "mode": "lite",
            "python": "py9",
            "src_dir": "/s",
            "profiles": ["/p"],
            "tools": {
                "tool-a": {"binary": "bina", "sandbox": str(self.sb("tool-a")), "mode": "lite"},
                "tool-b": {"binary": "binb", "sandbox": str(self.tr / "tool-b" / "alt"), "mode": "lite"},
            },
        }
        m.update(extra)
        return m

    # --- order of the up-front checks -------------------------------------------------------------

    def test_unknown_mode_checked_before_manifest_is_read(self):
        self.write_manifest("not json")
        with self.assertRaises(installer.InstallError) as cm:
            installer.set_mode(self.prefix, "bogus", [TA])
        self.assertEqual(str(cm.exception), "unknown mode: bogus")

    def test_corrupt_manifest_propagates_read_error(self):
        self.write_manifest("[1]")
        with self.assertRaises(installer.InstallError) as cm:
            installer.set_mode(self.prefix, "full", [TA])
        self.assertEqual(str(cm.exception), "manifest not a dict")

    def test_nothing_installed(self):
        for data in (None, {}, {"tools": {}}, {"mode": "lite"}):
            with self.subTest(data=data):
                if data is None:
                    if self.mp.exists():
                        self.mp.unlink()
                else:
                    self.write_manifest(data)
                with self.assertRaises(installer.InstallError) as cm:
                    installer.set_mode(self.prefix, "full", [TA])
                self.assertEqual(str(cm.exception), "nothing installed")
                self.assertFalse(self.bin.exists())

    def test_bad_entry_for_known_tool_refused_before_any_write(self):
        self.write_manifest(
            {"mode": "lite", "tools": {"tool-a": {"sandbox": str(self.sb("tool-a"))}, "tool-b": "junk"}}
        )
        before = self.mp.read_bytes()
        with self.assertRaises(installer.InstallError) as cm:
            installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertEqual(str(cm.exception), "bad manifest entry: tool-b")
        self.assertIsNone(cm.exception.__cause__)
        self.assertFalse(self.bin.exists())
        self.assertEqual(self.mp.read_bytes(), before)

    def test_bad_entry_for_missing_catalog_tool_is_skipped(self):
        self.write_manifest({"mode": "lite", "tools": {"tool-a": {}, "tool-z": "junk"}})
        out = installer.set_mode(self.prefix, "full", [TA])
        self.assertEqual(out, ["mode set to full", "missing catalog entry: tool-z" + MISSING_TAIL])
        m = self.manifest()
        self.assertEqual(m["tools"], {"tool-a": {"mode": "full"}, "tool-z": "junk"})
        self.assertEqual(m["mode"], "full")

    def test_render_error_wrapped_with_cause(self):
        self.write_manifest(self.two_tool_manifest())
        err = ValueError("boom")
        with mock.patch.object(launcher, "render_wrapper", side_effect=err):
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertEqual(str(cm.exception), "boom")
        self.assertIs(cm.exception.__cause__, err)
        self.assertFalse(self.bin.exists())

    def test_render_install_error_propagates_unchanged(self):
        self.write_manifest(self.two_tool_manifest())
        err = installer.InstallError("own")
        with mock.patch.object(launcher, "render_wrapper", side_effect=err):
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertIs(cm.exception, err)

    @unittest.skipIf(sys.platform == "win32", "file modes and chmod are not honoured on Windows")
    def test_render_arguments_and_defaults(self):
        self.write_manifest({"tools": {"tool-a": {}, "tool-b": {"sandbox": str(self.tr / "tool-b" / "alt")}}})
        calls = []

        def fake(t, rb, sb, rd, mode, py, sd):
            calls.append((t.id, rb, sb, rd, mode, py, sd))
            return f"wrapper {t.id}\n"

        with mock.patch.object(launcher, "render_wrapper", side_effect=fake):
            installer.set_mode(self.prefix, "full", [TB, TA])
        sa = self.sb("tool-a")
        alt = self.tr / "tool-b" / "alt"
        self.assertEqual(
            calls,
            [
                ("tool-a", sa / ".local" / "bin" / "bina", sa, self.run_dir, "full", "python3", Path(".")),
                ("tool-b", alt / ".local" / "bin" / "binb", alt, self.run_dir, "full", "python3", Path(".")),
            ],
        )
        self.assertEqual((self.bin / "bina").read_text(encoding="utf-8"), "wrapper tool-a\n")
        self.assertEqual(stat.S_IMODE((self.bin / "bina").stat().st_mode), 0o755)
        # No previous top-level mode: the manifest gets one, entries without a catalog tool are untouched.
        self.assertEqual(self.manifest()["mode"], "full")

    # --- reads before writing ---------------------------------------------------------------------

    def test_manifest_read_error_before_any_write(self):
        self.write_manifest(self.two_tool_manifest())
        real = Path.read_bytes
        mp = self.mp

        def rb(p):
            if p == mp:
                raise PermissionError("no read")
            return real(p)

        with mock.patch.object(Path, "read_bytes", rb):
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertEqual(str(cm.exception), "no read")
        self.assertIsInstance(cm.exception.__cause__, PermissionError)
        self.assertFalse(self.bin.exists())

    def test_wrapper_backup_read_error_before_any_write(self):
        self.write_manifest(self.two_tool_manifest())
        self.bin.mkdir(parents=True)
        (self.bin / "bina").write_text("old a", encoding="utf-8")
        (self.bin / "binb").write_text("old b", encoding="utf-8")
        real = Path.read_bytes
        target = self.bin / "binb"

        def rb(p):
            if p == target:
                raise OSError("eio")
            return real(p)

        before = self.mp.read_bytes()
        with mock.patch.object(Path, "read_bytes", rb):
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertEqual(str(cm.exception), "eio")
        self.assertEqual((self.bin / "bina").read_text(encoding="utf-8"), "old a")
        self.assertEqual(self.mp.read_bytes(), before)

    # --- success ----------------------------------------------------------------------------------

    @unittest.skipIf(sys.platform == "win32", "file modes and chmod are not honoured on Windows")
    def test_success_writes_wrappers_and_manifest(self):
        m = self.two_tool_manifest()
        m["tools"]["tool-c"] = {"binary": "binc"}
        m["tools"]["tool-d"] = {"binary": "bind", "mode": "full"}
        m["tools"]["tool-e"] = 7
        self.write_manifest(m)
        out = installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertEqual(
            out,
            [
                "mode set to full",
                "missing catalog entry: tool-c" + MISSING_TAIL,
                "missing catalog entry: tool-d" + MISSING_TAIL,
                "missing catalog entry: tool-e" + MISSING_TAIL,
            ],
        )
        got = self.manifest()
        self.assertEqual(got["mode"], "full")
        self.assertEqual(got["python"], "py9")
        self.assertEqual(got["src_dir"], "/s")
        self.assertEqual(got["profiles"], ["/p"])
        self.assertEqual(got["tools"]["tool-a"]["mode"], "full")
        self.assertEqual(got["tools"]["tool-b"]["mode"], "full")
        self.assertEqual(got["tools"]["tool-c"], {"binary": "binc", "mode": "lite"})
        self.assertEqual(got["tools"]["tool-d"], {"binary": "bind", "mode": "full"})
        self.assertEqual(got["tools"]["tool-e"], 7)
        self.assertEqual(stat.S_IMODE(self.mp.stat().st_mode), 0o600)
        sa = self.sb("tool-a")
        want_a = launcher.render_wrapper(
            TA, sa / ".local" / "bin" / "bina", sa, self.run_dir, "full", "py9", Path("/s")
        )
        self.assertEqual((self.bin / "bina").read_text(encoding="utf-8"), want_a)
        self.assertTrue((self.bin / "binb").is_file())
        self.assertFalse((self.bin / "binc").exists())

    def test_previous_mode_not_a_string_leaves_missing_entries_alone(self):
        self.write_manifest({"mode": 3, "tools": {"tool-a": {}, "tool-c": {}}})
        installer.set_mode(self.prefix, "lite", [TA])
        self.assertEqual(self.manifest()["tools"], {"tool-a": {"mode": "lite"}, "tool-c": {}})

    # --- rollback ---------------------------------------------------------------------------------

    def prepare_rollback(self):
        self.write_manifest(self.two_tool_manifest())
        self.bin.mkdir(parents=True)
        (self.bin / "bina").write_text("old a", encoding="utf-8")
        os.chmod(self.bin / "bina", 0o700)
        return self.mp.read_bytes()

    @unittest.skipIf(sys.platform == "win32", "file modes and chmod are not honoured on Windows")
    def test_manifest_write_failure_restores_wrappers(self):
        before = self.prepare_rollback()
        err = OSError("disk full")
        with mock.patch.object(installer, "_atomic_manifest", side_effect=err):
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertEqual(str(cm.exception), "disk full")
        self.assertIs(cm.exception.__cause__, err)
        self.assertEqual((self.bin / "bina").read_text(encoding="utf-8"), "old a")
        self.assertEqual(stat.S_IMODE((self.bin / "bina").stat().st_mode), 0o755)
        self.assertFalse((self.bin / "binb").exists())
        self.assertEqual(self.mp.read_bytes(), before)

    def test_wrapper_write_failure_rolls_back_in_order(self):
        self.prepare_rollback()
        real = installer._atomic_write_text
        seen = []

        def wt(p, text, mode):
            seen.append(p)
            if p.name == "binb":
                raise OSError("second")
            real(p, text, mode)

        with mock.patch.object(installer, "_atomic_write_text", side_effect=wt):
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertEqual(str(cm.exception), "second")
        self.assertEqual(seen, [self.bin / "bina", self.bin / "binb"])
        self.assertEqual((self.bin / "bina").read_text(encoding="utf-8"), "old a")

    def test_rollback_failures_reported_in_order(self):
        self.prepare_rollback()
        (self.bin / "binb").write_text("old b", encoding="utf-8")
        with (
            mock.patch.object(installer, "_atomic_manifest", side_effect=OSError("disk full")),
            mock.patch.object(installer, "_atomic_write_bytes", side_effect=OSError("nope")) as wb,
        ):
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(self.prefix, "full", [TA, TB])
        wa, wbp = self.bin / "bina", self.bin / "binb"
        self.assertEqual(str(cm.exception), f"disk full (rollback failed: {wa}: nope; {wbp}: nope)")
        self.assertEqual(
            [c.args for c in wb.call_args_list],
            [(wa, b"old a", 0o755), (wbp, b"old b", 0o755)],
        )

    def test_rollback_unlink_failure_reported(self):
        self.prepare_rollback()
        real_unlink = Path.unlink
        wbp = self.bin / "binb"

        def ul(p, *a, **k):
            if p == wbp:
                raise PermissionError("locked")
            return real_unlink(p, *a, **k)

        with (
            mock.patch.object(installer, "_atomic_manifest", side_effect=OSError("disk full")),
            mock.patch.object(Path, "unlink", ul),
        ):
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(self.prefix, "full", [TA, TB])
        self.assertEqual(str(cm.exception), f"disk full (rollback failed: {wbp}: locked)")

    def test_base_exception_during_write_is_not_caught(self):
        self.prepare_rollback()
        with mock.patch.object(installer, "_atomic_manifest", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                installer.set_mode(self.prefix, "full", [TA, TB])
        # No rollback ran: the new wrappers stay.
        self.assertNotEqual((self.bin / "bina").read_text(encoding="utf-8"), "old a")


class UninstallCharacterization(_Base):
    def setup_installed(self, profiles=None) -> None:
        m = {"mode": "lite", "tools": {"tool-a": {}}}
        if profiles is not None:
            m["profiles"] = profiles
        self.write_manifest(m)
        (self.tr / "bin").mkdir(parents=True, exist_ok=True)
        (self.tr / "bin" / "bina").write_text("w", encoding="utf-8")

    def test_nothing_to_remove(self):
        self.assertEqual(installer.uninstall(self.prefix), ["nothing to remove"])
        self.assertEqual(installer.uninstall(self.prefix, dry_run=True), ["nothing to remove"])
        self.write_manifest({})
        self.assertEqual(installer.uninstall(self.prefix), ["nothing to remove"])
        self.assertTrue(self.mp.exists())

    def test_corrupt_manifest_raises(self):
        self.write_manifest("{")
        with self.assertRaises(installer.InstallError):
            installer.uninstall(self.prefix)
        self.assertTrue(self.mp.exists())

    def test_symlinked_root_refused_first(self):
        real = self.base / "real"
        real.mkdir()
        (real / "manifest.json").write_text(json.dumps({"tools": {"x": {}}}), encoding="utf-8")
        self.prefix.mkdir()
        self.tr.symlink_to(real)
        with mock.patch.object(installer, "_inside", side_effect=AssertionError("not reached")):
            with self.assertRaises(installer.InstallError) as cm:
                installer.uninstall(self.prefix)
        self.assertEqual(str(cm.exception), f"refusing to remove symlink {self.tr}")
        self.assertTrue((real / "manifest.json").exists())

    def test_root_outside_prefix_refused(self):
        self.setup_installed()
        with mock.patch.object(installer, "_inside", return_value=False):
            with self.assertRaises(installer.InstallError) as cm:
                installer.uninstall(self.prefix)
        self.assertEqual(str(cm.exception), f"refusing to remove {self.tr} not inside {self.prefix}")
        self.assertTrue(self.tr.exists())

    def test_dry_run_lists_and_changes_nothing(self):
        self.setup_installed(profiles=["/a", "/b"])
        with mock.patch.object(launcher, "remove_profile_block", side_effect=AssertionError("no")):
            out = installer.uninstall(self.prefix, dry_run=True)
        self.assertEqual(
            out,
            ["would remove profile block from /a", "would remove profile block from /b", f"would remove {self.tr}"],
        )
        self.assertTrue(self.tr.exists())

    def test_dry_run_profiles_not_a_list(self):
        self.setup_installed(profiles="/a")
        self.assertEqual(installer.uninstall(self.prefix, dry_run=True), [f"would remove {self.tr}"])

    def test_real_run_profile_outcomes_and_removal(self):
        p_kept = self.base / "kept"
        p_kept.write_text("x", encoding="utf-8")
        p_gone = self.base / "gone"
        p_same = self.base / "same"
        p_err = self.base / "err"
        self.setup_installed(profiles=[str(p_kept), str(p_gone), str(p_same), str(p_err)])
        calls = []

        def rpb(p):
            calls.append(p)
            if p == p_err:
                raise ValueError("bad block")
            return p != p_same

        with mock.patch.object(launcher, "remove_profile_block", side_effect=rpb):
            out = installer.uninstall(self.prefix)
        self.assertEqual(calls, [p_kept, p_gone, p_same, p_err])
        self.assertEqual(
            out,
            [
                f"removed profile block from {p_kept}",
                f"removed {p_gone}",
                f"could not remove profile block from {p_err}: bad block",
                f"removed {self.tr}",
                f"removed {self.prefix}",
            ],
        )
        self.assertFalse(self.prefix.exists())

    def test_prefix_kept_when_not_empty(self):
        self.setup_installed()
        (self.prefix / "other").write_text("x", encoding="utf-8")
        self.assertEqual(installer.uninstall(self.prefix), [f"removed {self.tr}"])
        self.assertTrue(self.prefix.is_dir())

    def test_rmtree_failure_reported(self):
        self.setup_installed()
        with mock.patch.object(installer.shutil, "rmtree", side_effect=OSError("busy")):
            out = installer.uninstall(self.prefix)
        self.assertEqual(out, [f"could not remove {self.tr}: busy"])
        self.assertTrue(self.tr.exists())

    def test_root_replaced_by_file_reported(self):
        self.setup_installed(profiles=["/p"])
        tr = self.tr

        def swap(p):
            shutil.rmtree(tr)
            tr.write_text("f", encoding="utf-8")
            return False

        with mock.patch.object(launcher, "remove_profile_block", side_effect=swap):
            out = installer.uninstall(self.prefix)
        self.assertEqual(out, [f"could not remove {tr}: not a directory"])

    def test_root_vanished_reports_nothing_for_it(self):
        self.setup_installed(profiles=["/p"])
        tr = self.tr

        def drop(p):
            shutil.rmtree(tr)
            return False

        with mock.patch.object(launcher, "remove_profile_block", side_effect=drop):
            out = installer.uninstall(self.prefix)
        self.assertEqual(out, [f"removed {self.prefix}"])

    def test_prefix_removal_failure_reported(self):
        self.setup_installed()
        real_rmdir = Path.rmdir
        prefix = self.prefix

        def rd(p):
            if p == prefix:
                raise OSError("eperm")
            return real_rmdir(p)

        with mock.patch.object(Path, "rmdir", rd):
            out = installer.uninstall(self.prefix)
        self.assertEqual(out, [f"removed {self.tr}", f"could not remove {self.prefix}: eperm"])

    def test_base_exception_from_profile_removal_propagates(self):
        self.setup_installed(profiles=["/p"])
        with mock.patch.object(launcher, "remove_profile_block", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                installer.uninstall(self.prefix)
        self.assertTrue(self.tr.exists())


class ManifestSandboxContainment(_Base):
    """a manifest sandbox must resolve inside the tools root"""

    def _refused(self, sandbox):
        self.write_manifest({"mode": "lite", "tools": {"tool-a": {"sandbox": sandbox}}})
        before = self.mp.read_bytes()
        with self.assertRaises(installer.InstallError) as cm:
            installer.set_mode(self.prefix, "full", [TA])
        self.assertEqual(str(cm.exception), "bad sandbox in manifest entry: tool-a")
        self.assertFalse(self.bin.exists())
        self.assertEqual(self.mp.read_bytes(), before)

    def test_absolute_outside_refused(self):
        self._refused("/elsewhere/a")

    def test_dotdot_escape_refused(self):
        self._refused(str(self.tr / "tool-a" / ".." / ".." / "out"))

    def test_nul_and_non_string_refused(self):
        self._refused(str(self.sb("tool-a")) + "\0x")
        self._refused(["x"])
        self._refused("")

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_symlink_escape_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        link = self.tr / "tool-a" / "link"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside, target_is_directory=True)
        self._refused(str(link / "home"))

    def test_inside_and_default_accepted(self):
        self.write_manifest({"tools": {"tool-a": {"sandbox": str(self.sb("tool-a"))}}})
        self.assertEqual(installer.set_mode(self.prefix, "full", [TA]), ["mode set to full"])
        self.assertEqual(installer._manifest_sandbox(self.tr, "tool-a", {}), self.sb("tool-a"))


if __name__ == "__main__":
    unittest.main()
