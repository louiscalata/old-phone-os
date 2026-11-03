# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opos.aitools import catalog, installer, place, wizard


def _td(tid, binary, url, archs, min_free):
    """Build catalog tool dict; its installer_sha256 matches the b"ok" payload the fake fetches return."""
    return {"id": tid, "name": f"Name {tid}", "binary": binary, "installer_url": url, "interpreter": "sh", "installer_args": [], "installer_env": {}, "archs": list(archs), "min_free_mb": min_free, "state_subdir": f".{tid}", "run_env": {}, "lite_env": {}, "signin": f"signin {tid}", "docs_url": f"https://example.com/{tid}", "installer_sha256": hashlib.sha256(b"ok").hexdigest()}


def _write_cat(p, tools):
    """Write catalog json."""
    p.write_text(json.dumps({"version": 1, "tools": tools}), encoding="utf-8")


def _runner_all(bins):
    """Return runner creating given binaries."""
    def run(argv, env, timeout_s):
        """Fake runner."""
        sb = Path(env["HOME"])
        for b in bins:
            rp = sb / ".local" / "bin" / b
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
            os.chmod(rp, 0o755)
        return (0, "")

    return run


class TestPublicState(unittest.TestCase):
    """Tests for public_state."""

    def test_supported_linux_two_tools(self):
        """Supported linux reports ok and problem."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64", "aarch64"], 5)
            t2 = _td("tool-b", "tool-b-bin", "https://example.com/b.sh", ["aarch64"], 1000)
            _write_cat(cat, [t1, t2])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            orig = place.volatile_warning
            place.volatile_warning = lambda p, mounts_file=Path("/proc/mounts"): None
            try:
                out = wizard.public_state(env=env, home=home, machine="x86_64", platform="linux", free_mb_fn=lambda p: 10)
            finally:
                place.volatile_warning = orig
            self.assertTrue(out["supported"])
            self.assertEqual(out["prefix"], str(prefix))
            self.assertEqual(out["free_mb"], 10)
            self.assertEqual(out["arch"], "x86_64")
            self.assertIsNone(out["error"])
            self.assertEqual(len(out["tools"]), 2)
            a = next(x for x in out["tools"] if x["id"] == "tool-a")
            b = next(x for x in out["tools"] if x["id"] == "tool-b")
            self.assertTrue(a["ok"])
            self.assertIsNone(a["problem"])
            self.assertFalse(b["ok"])
            self.assertIsNotNone(b["problem"])
            self.assertIn("MB", b["problem"])

    def test_wrong_arch(self):
        """Wrong arch makes tool not ok."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["aarch64"], 5)
            _write_cat(cat, [t1])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            orig = place.volatile_warning
            place.volatile_warning = lambda p, mounts_file=Path("/proc/mounts"): None
            try:
                out = wizard.public_state(env=env, home=home, machine="x86_64", platform="linux", free_mb_fn=lambda p: 100)
            finally:
                place.volatile_warning = orig
            self.assertFalse(out["tools"][0]["ok"])
            self.assertIn("x86_64", out["tools"][0]["problem"])

    def test_unsupported_platform(self):
        """Unsupported platform returns not supported."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            _write_cat(cat, [_td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            orig = place.volatile_warning
            place.volatile_warning = lambda p, mounts_file=Path("/proc/mounts"): None
            try:
                out = wizard.public_state(env=env, home=home, machine="x86_64", platform="win32", free_mb_fn=lambda p: 100)
            finally:
                place.volatile_warning = orig
            self.assertFalse(out["supported"])
            self.assertEqual(out["tools"], [])
            self.assertIsNone(out["error"])

    def test_broken_catalog(self):
        """Broken catalog yields error and no tools."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            cat.write_text("{ bad", encoding="utf-8")
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            orig = place.volatile_warning
            place.volatile_warning = lambda p, mounts_file=Path("/proc/mounts"): None
            try:
                out = wizard.public_state(env=env, home=home, machine="x86_64", platform="linux", free_mb_fn=lambda p: 100)
            finally:
                place.volatile_warning = orig
            self.assertTrue(out["supported"])
            self.assertIsNotNone(out["error"])
            self.assertEqual(out["tools"], [])

    def test_never_raises(self):
        """public_state never raises on bad inputs."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            _write_cat(cat, [_td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}

            def bad_free(p):
                raise RuntimeError("boom")

            orig = place.volatile_warning
            place.volatile_warning = lambda p, mounts_file=Path("/proc/mounts"): (_ for _ in ()).throw(RuntimeError("vw"))
            try:
                out = wizard.public_state(env=env, home=home, machine="x86_64", platform="linux", free_mb_fn=bad_free)
                self.assertIsNotNone(out)
                cat.write_text("bad", encoding="utf-8")
                out2 = wizard.public_state(env=env, home=home, machine="badarch", platform="linux", free_mb_fn=bad_free)
                self.assertIsNotNone(out2)
            finally:
                place.volatile_warning = orig


class TestInstallSelected(unittest.TestCase):
    """Tests for install_selected."""

    def test_dry_run_writes_nothing(self):
        """Dry run reports would and writes nothing."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            _write_cat(cat, [t1])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            out = wizard.install_selected(["tool-a"], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, dry_run=True, free_mb_fn=lambda p: 100000)
            self.assertTrue(any("would" in s.lower() for s in out))
            self.assertFalse((prefix / "opos-tools").exists())

    def test_real_run_fake_runner(self):
        """Real run with fake fetch creates files."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            _write_cat(cat, [t1])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            runner = _runner_all(["tool-a-bin"])
            out = wizard.install_selected(["tool-a"], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, fetch=lambda u: b"ok", runner=runner, free_mb_fn=lambda p: 100000)
            self.assertTrue(any("installed" in s for s in out))
            self.assertTrue((prefix / "opos-tools" / "manifest.json").exists())
            self.assertTrue((prefix / "opos-tools" / "bin" / "tool-a-bin").exists())
            self.assertIn("Sign in yourself", out[-1])

    def test_error_cases(self):
        """Bad inputs raise with correct messages."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            _write_cat(cat, [t1])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            with self.assertRaises(installer.InstallError) as cm:
                wizard.install_selected(["tool-a"], "badmode", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src)
            self.assertIn("unknown mode", str(cm.exception).lower())
            with self.assertRaises(installer.InstallError) as cm:
                wizard.install_selected([], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src)
            self.assertIn("no tools selected", str(cm.exception).lower())
            with self.assertRaises(installer.InstallError) as cm:
                wizard.install_selected(["tool-a", "tool-a"], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src)
            self.assertIn("duplicate", str(cm.exception).lower())
            with self.assertRaises(installer.InstallError) as cm:
                wizard.install_selected(["tool-a"], "lite", env=env, home=home, machine="x86_64", platform="win32", python="python3", src_dir=src)
            self.assertIn("unsupported platform", str(cm.exception).lower())
            with self.assertRaises(installer.InstallError) as cm:
                wizard.install_selected(["unknown-id"], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, free_mb_fn=lambda p: 100000)
            self.assertIn("unknown-id", str(cm.exception))
            with self.assertRaises(installer.InstallError) as cm:
                wizard.install_selected(["tool-a"], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, free_mb_fn=lambda p: 1)
            self.assertTrue(str(cm.exception).startswith("tool-a:"))

    def test_second_failing_leaves_first(self):
        """Second tool failing keeps first installed."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            t2 = _td("tool-b", "tool-b-bin", "https://example.com/b.sh", ["x86_64"], 5)
            _write_cat(cat, [t1, t2])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}

            def fetch(url):
                if url == t2["installer_url"]:
                    raise installer.InstallError("fetch fail")
                return b"ok"

            runner = _runner_all(["tool-a-bin", "tool-b-bin"])
            with self.assertRaises(installer.InstallError) as cm:
                wizard.install_selected(["tool-a", "tool-b"], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, fetch=fetch, runner=runner, free_mb_fn=lambda p: 100000)
            self.assertTrue(str(cm.exception).startswith("tool-b:"))
            m = json.loads((prefix / "opos-tools" / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("tool-a", m["tools"])
            self.assertNotIn("tool-b", m["tools"])
            self.assertTrue((prefix / "opos-tools" / "bin" / "tool-a-bin").exists())


class TestUninstallAll(unittest.TestCase):
    """Tests for uninstall_all."""

    def test_nothing_installed(self):
        """No manifest reports nothing to remove."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            env = {"OPOS_TOOLS_PREFIX": str(prefix)}
            with mock.patch.object(wizard, "_FALLBACK_PREFIXES", ()):
                out = wizard.uninstall_all(env=env, home=home)
            self.assertEqual(out, ["nothing to remove"])

    def test_after_install_removes_everything(self):
        """After install uninstall restores snapshot."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            _write_cat(cat, [t1])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            (home / ".profile").write_text("# mine\n", encoding="utf-8")
            src = td / "src"
            src.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            snap = set(td.rglob("*"))
            runner = _runner_all(["tool-a-bin"])
            wizard.install_selected(["tool-a"], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, fetch=lambda u: b"ok", runner=runner, free_mb_fn=lambda p: 100000)
            self.assertTrue((prefix / "opos-tools").exists())
            with mock.patch.object(wizard, "_FALLBACK_PREFIXES", ()):
                out = wizard.uninstall_all(env=env, home=home)
            self.assertTrue(any("removed" in s for s in out))
            self.assertFalse((prefix / "opos-tools").exists())
            self.assertEqual((home / ".profile").read_text(encoding="utf-8"), "# mine\n")
            after = set(td.rglob("*"))
            self.assertEqual(snap, after)

    def test_dry_run(self):
        """Dry run does not delete."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            _write_cat(cat, [t1])
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            env = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(prefix)}
            runner = _runner_all(["tool-a-bin"])
            wizard.install_selected(["tool-a"], "lite", env=env, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, fetch=lambda u: b"ok", runner=runner, free_mb_fn=lambda p: 100000)
            with mock.patch.object(wizard, "_FALLBACK_PREFIXES", ()):
                out = wizard.uninstall_all(env=env, home=home, dry_run=True)
            self.assertTrue(any("would remove" in s for s in out))
            self.assertTrue((prefix / "opos-tools").exists())

    def test_failure_reported(self):
        """Failures reported as could not remove."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "pfx"
            home = td / "home"
            home.mkdir()
            env = {"OPOS_TOOLS_PREFIX": str(prefix)}
            orig = installer.uninstall

            def bad(prefix_arg, dry_run=False):
                raise installer.InstallError("boom")

            installer.uninstall = bad
            try:
                with mock.patch.object(wizard, "_FALLBACK_PREFIXES", ()):
                    out = wizard.uninstall_all(env=env, home=home)
                self.assertTrue(out[0].startswith("could not remove"))
            finally:
                installer.uninstall = orig
            orig2 = place.choose_prefix

            def bad_pref(env_arg, home_arg, persistent_roots=("/userdata",)):
                raise OSError("no prefix")

            place.choose_prefix = bad_pref
            try:
                with mock.patch.object(wizard, "_FALLBACK_PREFIXES", ()):
                    out2 = wizard.uninstall_all(env=env, home=home)
                self.assertTrue(out2[0].startswith("could not remove"))
            finally:
                place.choose_prefix = orig2

    def test_fallback_removes_old_prefix(self):
        """Fallback prefix removed when explicit prefix absent."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            _write_cat(cat, [t1])
            old = td / "old"
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            env_install = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(old)}
            runner = _runner_all(["tool-a-bin"])
            wizard.install_selected(["tool-a"], "lite", env=env_install, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, fetch=lambda u: b"ok", runner=runner, free_mb_fn=lambda p: 100000)
            self.assertTrue((old / "opos-tools").exists())
            env_uninstall = {"OPOS_AI_TOOLS_TABLE": str(cat)}
            with mock.patch.object(wizard, "_FALLBACK_PREFIXES", (old,)):
                out = wizard.uninstall_all(env=env_uninstall, home=home)
            self.assertFalse((old / "opos-tools").exists())
            self.assertTrue(any("removed" in s for s in out))

    def test_explicit_prefix_ignores_fallback(self):
        """Explicit prefix examines only that prefix."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            _write_cat(cat, [t1])
            old = td / "old"
            new = td / "new"
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            env_install = {"OPOS_AI_TOOLS_TABLE": str(cat), "OPOS_TOOLS_PREFIX": str(old)}
            runner = _runner_all(["tool-a-bin"])
            wizard.install_selected(["tool-a"], "lite", env=env_install, home=home, machine="x86_64", platform="linux", python="python3", src_dir=src, fetch=lambda u: b"ok", runner=runner, free_mb_fn=lambda p: 100000)
            self.assertTrue((old / "opos-tools").exists())
            env_uninstall = {"OPOS_TOOLS_PREFIX": str(new), "OPOS_AI_TOOLS_TABLE": str(cat)}
            with mock.patch.object(wizard, "_FALLBACK_PREFIXES", (old,)):
                out = wizard.uninstall_all(env=env_uninstall, home=home)
            self.assertTrue((old / "opos-tools").exists())
            self.assertEqual(out, ["nothing to remove"])

    def test_nothing_anywhere_gives_nothing(self):
        """Nothing anywhere reports nothing to remove."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            home = td / "home"
            home.mkdir()
            old = td / "old"
            old.mkdir()
            env = {}
            with mock.patch.object(wizard, "_FALLBACK_PREFIXES", (old,)):
                out = wizard.uninstall_all(env=env, home=home)
            self.assertEqual(out, ["nothing to remove"])
            with mock.patch.object(wizard, "_FALLBACK_PREFIXES", ()):
                out2 = wizard.uninstall_all(env=env, home=home)
            self.assertEqual(out2, ["nothing to remove"])

    def test_home_local_share_is_candidate(self):
        """Home local share candidate is examined."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cat = td / "cat.json"
            t1 = _td("tool-a", "tool-a-bin", "https://example.com/a.sh", ["x86_64"], 5)
            _write_cat(cat, [t1])
            home = td / "home"
            home.mkdir()
            src = td / "src"
            src.mkdir()
            prefix_home = home / ".local" / "share" / "opos"
            tools = catalog.load(cat)
            tool = catalog.get(tools, "tool-a")
            installer.install(tool, prefix=prefix_home, mode="lite", machine="x86_64", home=home, python="python3", src_dir=src, fetch=lambda u: b"ok", runner=_runner_all(["tool-a-bin"]), free_mb_fn=lambda p: 100000)
            self.assertTrue((prefix_home / "opos-tools").exists())
            with mock.patch.object(wizard, "_FALLBACK_PREFIXES", ()):
                out = wizard.uninstall_all(env={}, home=home)
            self.assertFalse((prefix_home / "opos-tools").exists())
            self.assertTrue(any("removed" in s for s in out))
