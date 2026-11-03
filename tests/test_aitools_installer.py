# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from opos.aitools import installer, launcher
from opos.aitools.catalog import Tool


FAKE_HOME = "/" + "home/real"


def _sha(data: bytes) -> str:
    """sha256 hex of data"""
    return hashlib.sha256(data).hexdigest()


def _tool(**over) -> Tool:
    """make Tool with defaults; its installer_sha256 matches _fetch_ok()'s default payload"""
    base = dict(
        id="test-tool",
        name="Test Tool",
        binary="testbin",
        installer_url="https://example.com/install.sh",
        interpreter="sh",
        installer_args=(),
        installer_env={},
        archs=("x86_64",),
        min_free_mb=1,
        state_subdir="state",
        run_env={},
        lite_env={},
        signin="run testbin login",
        docs_url="https://example.com/docs",
        installer_sha256=_sha(b"echo hi"),
    )
    base.update(over)
    return Tool(**base)  # type: ignore[arg-type]


def _fetch_ok(data=b"echo hi"):
    """fetch stub"""
    calls = []

    def fn(url):
        calls.append(url)
        return data

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def _runner_for(expected: dict[str, str], fail=False, create=True):
    """runner that creates binary under HOME"""
    calls = []

    def fn(argv, env, timeout_s):
        calls.append((list(argv), dict(env), timeout_s))
        if fail:
            return 1, "fail tail"
        if create:
            home = Path(env["HOME"])
            tid = home.parent.name
            binary = expected.get(tid, list(expected.values())[0] if expected else "testbin")
            p = home / ".local" / "bin" / binary
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("#!/bin/sh\necho hi\n")
            p.chmod(0o755)
        return 0, "ok"

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def _snapshot(root: Path):
    """list all files under root"""
    if not root.exists():
        return set()
    out = set()
    for p in root.rglob("*"):
        if p.is_file():
            out.add(p.relative_to(root).as_posix())
    return out


class TestInstaller(unittest.TestCase):
    def test_dry_run_touches_nothing(self):
        t = _tool()
        fetch = _fetch_ok()
        runner = _runner_for({t.id: t.binary})
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            profile = home / ".profile"
            out = installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=fetch,
                runner=runner,
                profiles=[profile],
                dry_run=True,
            )
            self.assertTrue(any("would install" in s for s in out))
            self.assertTrue(any(t.signin in s for s in out))
            self.assertEqual(fetch.calls, [])  # type: ignore[attr-defined]
            self.assertEqual(runner.calls, [])  # type: ignore[attr-defined]
            self.assertFalse(installer.tools_root(prefix).exists())
            self.assertFalse(installer.manifest_path(prefix).exists())
            self.assertFalse(profile.exists())

    def test_preflight_bad_arch_before_fetch(self):
        t = _tool(archs=("aarch64",))
        fetch = _fetch_ok()
        runner = _runner_for({t.id: t.binary})
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            with self.assertRaises(installer.InstallError) as cm:
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=fetch,
                    runner=runner,
                )
            self.assertIn("arch", str(cm.exception).lower())
            self.assertEqual(fetch.calls, [])  # type: ignore[attr-defined]
            self.assertEqual(runner.calls, [])  # type: ignore[attr-defined]

    def test_preflight_low_space_before_fetch(self):
        t = _tool(min_free_mb=100)
        fetch = _fetch_ok()
        runner = _runner_for({t.id: t.binary})
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            with self.assertRaises(installer.InstallError) as cm:
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=fetch,
                    runner=runner,
                    free_mb_fn=lambda p: 1,
                )
            self.assertIn("MB", str(cm.exception))
            self.assertEqual(fetch.calls, [])  # type: ignore[attr-defined]

    def test_runner_env_home_is_sandbox(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            exp_sandbox = installer.tools_root(prefix) / t.id / "home"
            captured = {}

            def runner(argv, env, timeout_s):
                captured.update(env)
                home_p = Path(env["HOME"])
                p = home_p / ".local" / "bin" / t.binary
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x")
                p.chmod(0o755)
                return 0, "ok"

            fetch = _fetch_ok()
            with mock.patch.dict(os.environ, {"HOME": FAKE_HOME, "PATH": "/usr/bin:/bin"}, clear=False):
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=fetch,
                    runner=runner,
                    profiles=[],
                )
            self.assertEqual(captured["HOME"], str(exp_sandbox))
            self.assertNotEqual(captured["HOME"], FAKE_HOME)
            self.assertIn("PATH", captured)
            self.assertEqual(captured["LANG"], "C.UTF-8")
            self.assertEqual(captured["TERM"], "dumb")

    def test_runner_env_no_api_key_leak(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            seen = {}

            def runner(argv, env, timeout_s):
                seen.update(env)
                p = Path(env["HOME"]) / ".local" / "bin" / t.binary
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x")
                p.chmod(0o755)
                return 0, "ok"

            fetch = _fetch_ok()
            env_patch = {"SOME_API_KEY": "secret123", "MY_TOKEN": "tok", "PASSWORD_FOO": "pw", "SECRET_STUFF": "s"}
            with mock.patch.dict(os.environ, env_patch, clear=False):
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=fetch,
                    runner=runner,
                    profiles=[],
                )
            for k in env_patch:
                self.assertNotIn(k, seen)
            for k in list(seen.keys()):
                self.assertFalse(any(bad in k for bad in ("KEY", "TOKEN", "SECRET", "PASSWORD")))

    def test_temp_script_removed_after_success(self):
        t = _tool(installer_sha256=_sha(b"data"))
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            script_path = []

            def runner(argv, env, timeout_s):
                script_path.append(argv[1])
                p = Path(env["HOME"]) / ".local" / "bin" / t.binary
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x")
                p.chmod(0o755)
                return 0, "ok"

            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(b"data"),
                runner=runner,
                profiles=[],
            )
            self.assertEqual(len(script_path), 1)
            self.assertFalse(Path(script_path[0]).exists())

    def test_temp_script_removed_after_failure(self):
        t = _tool(installer_sha256=_sha(b"data"))
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            script_path = []

            def runner(argv, env, timeout_s):
                script_path.append(argv[1])
                return 1, "boom"

            with self.assertRaises(installer.InstallError):
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=_fetch_ok(b"data"),
                    runner=runner,
                    profiles=[],
                )
            self.assertEqual(len(script_path), 1)
            self.assertFalse(Path(script_path[0]).exists())

    def test_failure_leaves_no_dir_no_manifest_when_fresh(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t.id: t.binary}, fail=True)
            with self.assertRaises(installer.InstallError):
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=_fetch_ok(),
                    runner=runner,
                    profiles=[],
                )
            tid_dir = installer.tools_root(prefix) / t.id
            self.assertFalse(tid_dir.exists())
            self.assertFalse(installer.manifest_path(prefix).exists())

    def test_failure_keeps_existing_install(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            ok_runner = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(),
                runner=ok_runner,
                profiles=[],
            )
            self.assertTrue(installer.manifest_path(prefix).exists())
            tid_dir = installer.tools_root(prefix) / t.id
            self.assertTrue(tid_dir.exists())
            fail_runner = _runner_for({t.id: t.binary}, fail=True)
            with self.assertRaises(installer.InstallError):
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=_fetch_ok(),
                    runner=fail_runner,
                    profiles=[],
                    force=True,
                )
            self.assertTrue(tid_dir.exists())
            self.assertTrue(installer.manifest_path(prefix).exists())
            cur = installer.read_manifest(prefix)
            self.assertIn(t.id, cur.get("tools", {}))

    def test_missing_binary_after_success_raises(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()

            def runner(argv, env, timeout_s):
                return 0, "ok"

            with self.assertRaises(installer.InstallError) as cm:
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=_fetch_ok(),
                    runner=runner,
                    profiles=[],
                )
            self.assertIn("did not create", str(cm.exception))

    @unittest.skipIf(sys.platform == "win32", "file modes and chmod are not honoured on Windows")
    def test_manifest_content_and_mode(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            profile = home / "prof"
            runner = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="full",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(),
                runner=runner,
                profiles=[profile],
            )
            mp = installer.manifest_path(prefix)
            self.assertTrue(mp.exists())
            mode = stat.S_IMODE(mp.stat().st_mode)
            self.assertEqual(mode, 0o600)
            data = json.loads(mp.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["mode"], "full")
            self.assertEqual(data["python"], "python3")
            self.assertEqual(data["src_dir"], "/fake/src")
            self.assertIn(str(profile), data["profiles"])
            self.assertIn(t.id, data["tools"])
            self.assertEqual(data["tools"][t.id]["binary"], t.binary)
            self.assertEqual(data["tools"][t.id]["signin"], t.signin)
            self.assertTrue(data["tools"][t.id]["sandbox"].endswith(f"{t.id}/home"))

    def test_profiles_block_and_uninstall(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            prefix.mkdir(parents=True, exist_ok=True)
            sentinel = prefix / "keep.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            sentinel.chmod(0o640)
            sentinel_bytes = sentinel.read_bytes()
            sentinel_mode = stat.S_IMODE(sentinel.stat().st_mode)
            home = td / "home"
            home.mkdir()
            p1 = home / "p1"
            p2 = home / "p2"
            p1.write_text("orig\n", encoding="utf-8")
            p1.chmod(0o640)
            p1_bytes = p1.read_bytes()
            p1_mode = stat.S_IMODE(p1.stat().st_mode)
            runner = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(),
                runner=runner,
                profiles=[p1, p2],
            )
            self.assertIn(launcher.BEGIN, p1.read_text(encoding="utf-8"))
            self.assertIn(launcher.BEGIN, p2.read_text(encoding="utf-8"))
            self.assertTrue(installer.tools_root(prefix).exists())
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_bytes(), sentinel_bytes)
            if sys.platform != "win32":
                self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), sentinel_mode)
            out = installer.uninstall(prefix)
            self.assertTrue(any("removed" in s for s in out))
            self.assertTrue(p1.exists())
            self.assertEqual(p1.read_bytes(), p1_bytes)
            if sys.platform != "win32":
                self.assertEqual(stat.S_IMODE(p1.stat().st_mode), p1_mode)
            self.assertNotIn(launcher.BEGIN, p1.read_text(encoding="utf-8"))
            self.assertFalse(p2.exists())
            self.assertFalse(installer.tools_root(prefix).exists())
            self.assertFalse(installer.manifest_path(prefix).exists())
            self.assertTrue(prefix.exists())
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_bytes(), sentinel_bytes)
            if sys.platform != "win32":
                self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), sentinel_mode)

    def test_uninstall_profile_removal_failure_reports(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            p1 = home / "p1"
            runner = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(),
                runner=runner,
                profiles=[p1],
            )
            with mock.patch.object(launcher, "remove_profile_block", side_effect=RuntimeError("boom")):
                out = installer.uninstall(prefix)
            self.assertTrue(any(s.startswith("could not remove") for s in out))
            self.assertTrue(installer.tools_root(prefix).exists() is False or any("could not remove" in s for s in out))

    def test_uninstall_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            prefix.mkdir()
            target = td / "target"
            target.mkdir()
            mp = target / "manifest.json"
            mp.write_text(json.dumps({"version": 1, "mode": "lite", "tools": {"x": {}}}))
            link = prefix / "opos-tools"
            link.symlink_to(target)
            with self.assertRaises(installer.InstallError) as cm:
                installer.uninstall(prefix)
            self.assertIn("symlink", str(cm.exception).lower())

    def test_install_refuses_symlink_tool_dir(self):
        """tool dir symlink aborts install"""
        t = _tool()
        fetch = _fetch_ok()
        runner = _runner_for({t.id: t.binary})
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            with tempfile.TemporaryDirectory() as extd:
                ext = Path(extd)
                marker = ext / "keep.txt"
                marker.write_text("keep")
                before = _snapshot(ext)
                tr = installer.tools_root(prefix)
                tid_dir = tr / t.id
                tid_dir.parent.mkdir(parents=True, exist_ok=True)
                tid_dir.symlink_to(ext)
                with self.assertRaises(installer.InstallError) as cm:
                    installer.install(
                        t,
                        prefix=prefix,
                        mode="lite",
                        machine="x86_64",
                        home=home,
                        python="python3",
                        src_dir=Path("/fake/src"),
                        fetch=fetch,
                        runner=runner,
                        profiles=[],
                    )
                self.assertIn("symlink", str(cm.exception).lower())
                self.assertEqual(fetch.calls, [])  # type: ignore[attr-defined]
                self.assertEqual(runner.calls, [])  # type: ignore[attr-defined]
                self.assertEqual(_snapshot(ext), before)
                self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
                self.assertFalse(installer.manifest_path(prefix).exists())
                self.assertFalse((tr / "bin").exists())

    def test_install_refuses_symlink_sandbox(self):
        """sandbox symlink aborts install"""
        t = _tool()
        fetch = _fetch_ok()
        runner = _runner_for({t.id: t.binary})
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            with tempfile.TemporaryDirectory() as extd:
                ext = Path(extd)
                marker = ext / "keep.txt"
                marker.write_text("keep")
                before = _snapshot(ext)
                tr = installer.tools_root(prefix)
                tid_dir = tr / t.id
                tid_dir.mkdir(parents=True, exist_ok=True)
                sandbox = tid_dir / "home"
                sandbox.symlink_to(ext)
                with self.assertRaises(installer.InstallError) as cm:
                    installer.install(
                        t,
                        prefix=prefix,
                        mode="lite",
                        machine="x86_64",
                        home=home,
                        python="python3",
                        src_dir=Path("/fake/src"),
                        fetch=fetch,
                        runner=runner,
                        profiles=[],
                    )
                self.assertIn("symlink", str(cm.exception).lower())
                self.assertEqual(fetch.calls, [])  # type: ignore[attr-defined]
                self.assertEqual(runner.calls, [])  # type: ignore[attr-defined]
                self.assertEqual(_snapshot(ext), before)
                self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
                self.assertFalse(installer.manifest_path(prefix).exists())

    def test_set_mode_rewrites(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(),
                runner=runner,
                profiles=[],
            )
            wrapper = installer.tools_root(prefix) / "bin" / t.binary
            lite_text = wrapper.read_text()
            out = installer.set_mode(prefix, "full", [t])
            self.assertIn("mode set", out[0])
            full_text = wrapper.read_text()
            self.assertNotEqual(lite_text, full_text)
            cur = installer.read_manifest(prefix)
            self.assertEqual(cur["mode"], "full")
            self.assertNotIn("Plug in power", full_text)

    def test_skip_fetch_when_binary_exists(self):
        t = _tool(installer_sha256=_sha(b"first"))
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner1 = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(b"first"),
                runner=runner1,
                profiles=[],
            )
            fetch2 = _fetch_ok(b"second")
            runner2 = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=fetch2,
                runner=runner2,
                profiles=[],
            )
            self.assertEqual(fetch2.calls, [])  # type: ignore[attr-defined]
            self.assertEqual(runner2.calls, [])  # type: ignore[attr-defined]

    def test_second_tool_keeps_first(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            mapping = {t1.id: t1.binary, t2.id: t2.binary}
            runner = _runner_for(mapping)
            fetch = _fetch_ok()
            installer.install(
                t1,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=fetch,
                runner=runner,
                profiles=[],
            )
            installer.install(
                t2,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=fetch,
                runner=runner,
                profiles=[],
            )
            cur = installer.read_manifest(prefix)
            self.assertIn(t1.id, cur["tools"])
            self.assertIn(t2.id, cur["tools"])
            self.assertTrue((installer.tools_root(prefix) / "bin" / t1.binary).exists())
            self.assertTrue((installer.tools_root(prefix) / "bin" / t2.binary).exists())

    def test_second_tool_mode_change_regenerates_all_wrappers(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            w1_lite = (installer.tools_root(prefix) / "bin" / t1.binary).read_bytes()
            installer.install(t2, prefix=prefix, mode="full", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            cur = installer.read_manifest(prefix)
            self.assertEqual(cur["mode"], "full")
            tr = installer.tools_root(prefix)
            sb1 = tr / t1.id / "home"
            rb1 = sb1 / ".local" / "bin" / t1.binary
            sb2 = tr / t2.id / "home"
            rb2 = sb2 / ".local" / "bin" / t2.binary
            exp1 = launcher.render_wrapper(t1, rb1, sb1, tr / "run", "full", "python3", Path("/fake/src"))
            exp2 = launcher.render_wrapper(t2, rb2, sb2, tr / "run", "full", "python3", Path("/fake/src"))
            self.assertEqual((tr / "bin" / t1.binary).read_text(encoding="utf-8"), exp1)
            self.assertEqual((tr / "bin" / t2.binary).read_text(encoding="utf-8"), exp2)
            self.assertNotEqual((tr / "bin" / t1.binary).read_bytes(), w1_lite)

    def test_second_tool_mode_change_missing_catalog_fails_clean(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[])
            w1_before = (installer.tools_root(prefix) / "bin" / t1.binary).read_bytes()
            mp_before = installer.manifest_path(prefix).read_bytes()
            snap_before = _snapshot(installer.tools_root(prefix))
            with self.assertRaises(installer.InstallError):
                installer.install(t2, prefix=prefix, mode="full", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t2])
            self.assertEqual((installer.tools_root(prefix) / "bin" / t1.binary).read_bytes(), w1_before)
            self.assertEqual(installer.manifest_path(prefix).read_bytes(), mp_before)
            self.assertEqual(_snapshot(installer.tools_root(prefix)), snap_before)
            self.assertFalse((installer.tools_root(prefix) / t2.id).exists())
            self.assertFalse((installer.tools_root(prefix) / "bin" / t2.binary).exists())

    def test_second_tool_render_failure_restores_all(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            w1_before = (installer.tools_root(prefix) / "bin" / t1.binary).read_bytes()
            mp_before = installer.manifest_path(prefix).read_bytes()
            snap_before = _snapshot(installer.tools_root(prefix))
            real_render = launcher.render_wrapper

            def flaky(t, rb, sb, run_dir, mode, py, sd):
                if t.id == t2.id:
                    raise RuntimeError("render boom")
                return real_render(t, rb, sb, run_dir, mode, py, sd)

            with mock.patch.object(launcher, "render_wrapper", side_effect=flaky):
                with self.assertRaises(installer.InstallError):
                    installer.install(t2, prefix=prefix, mode="full", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            self.assertEqual((installer.tools_root(prefix) / "bin" / t1.binary).read_bytes(), w1_before)
            self.assertEqual(installer.manifest_path(prefix).read_bytes(), mp_before)
            self.assertEqual(_snapshot(installer.tools_root(prefix)), snap_before)
            self.assertNotIn(t2.id, installer.read_manifest(prefix).get("tools", {}))

    def test_set_mode_failure_restores_all(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary})
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=_fetch_ok(), runner=runner, profiles=[])
            w_before = (installer.tools_root(prefix) / "bin" / t1.binary).read_bytes()
            mp_before = installer.manifest_path(prefix).read_bytes()
            with mock.patch.object(launcher, "render_wrapper", side_effect=RuntimeError("boom")):
                with self.assertRaises(installer.InstallError):
                    installer.set_mode(prefix, "full", [t1])
            self.assertEqual((installer.tools_root(prefix) / "bin" / t1.binary).read_bytes(), w_before)
            self.assertEqual(installer.manifest_path(prefix).read_bytes(), mp_before)
            self.assertEqual(installer.read_manifest(prefix)["mode"], "lite")

    def test_install_mid_write_restores_all(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            prof = home / "prof"
            prof.write_text("orig-line\n", encoding="utf-8")
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[prof], all_tools=[t1, t2])
            installer.install(t2, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[prof], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            w1_before = (tr / "bin" / t1.binary).read_bytes()
            w2_before = (tr / "bin" / t2.binary).read_bytes()
            env_before = (tr / "env.sh").read_bytes()
            mp_before = installer.manifest_path(prefix).read_bytes()
            prof_before = prof.read_bytes()
            snap_before = _snapshot(tr)
            orig_write_text = Path.write_text
            orig_write_bytes = Path.write_bytes

            def fake_write_text(self, data, encoding=None, errors=None, newline=None):
                if self.name in ("binb", ".binb.tmp"):
                    raise OSError(28, "No space left on device")
                return orig_write_text(self, data, encoding=encoding, errors=errors)

            def fake_write_bytes(self, data):
                if self.name in ("binb", ".binb.tmp"):
                    raise OSError(28, "No space left on device")
                return orig_write_bytes(self, data)

            with mock.patch.object(Path, "write_text", fake_write_text):
                with mock.patch.object(Path, "write_bytes", fake_write_bytes):
                    with self.assertRaises(installer.InstallError):
                        installer.install(t2, prefix=prefix, mode="full", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[prof], all_tools=[t1, t2])
            self.assertEqual((tr / "bin" / t1.binary).read_bytes(), w1_before)
            self.assertEqual((tr / "bin" / t2.binary).read_bytes(), w2_before)
            self.assertEqual((tr / "env.sh").read_bytes(), env_before)
            self.assertEqual(installer.manifest_path(prefix).read_bytes(), mp_before)
            self.assertEqual(prof.read_bytes(), prof_before)
            self.assertEqual(_snapshot(tr), snap_before)

    def test_set_mode_mid_write_restores_all(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            prof = home / "prof"
            prof.write_text("orig-line\n", encoding="utf-8")
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[prof], all_tools=[t1, t2])
            installer.install(t2, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[prof], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            w1_before = (tr / "bin" / t1.binary).read_bytes()
            w2_before = (tr / "bin" / t2.binary).read_bytes()
            env_before = (tr / "env.sh").read_bytes()
            mp_before = installer.manifest_path(prefix).read_bytes()
            prof_before = prof.read_bytes()
            orig_write_text = Path.write_text
            orig_write_bytes = Path.write_bytes

            def fake_write_text(self, data, encoding=None, errors=None, newline=None):
                if self.name in ("binb", ".binb.tmp"):
                    raise OSError(28, "No space left on device")
                return orig_write_text(self, data, encoding=encoding, errors=errors)

            def fake_write_bytes(self, data):
                if self.name in ("binb", ".binb.tmp"):
                    raise OSError(28, "No space left on device")
                return orig_write_bytes(self, data)

            with mock.patch.object(Path, "write_text", fake_write_text):
                with mock.patch.object(Path, "write_bytes", fake_write_bytes):
                    with self.assertRaises(installer.InstallError):
                        installer.set_mode(prefix, "full", [t1, t2])
            self.assertEqual((tr / "bin" / t1.binary).read_bytes(), w1_before)
            self.assertEqual((tr / "bin" / t2.binary).read_bytes(), w2_before)
            self.assertEqual((tr / "env.sh").read_bytes(), env_before)
            self.assertEqual(installer.manifest_path(prefix).read_bytes(), mp_before)
            self.assertEqual(prof.read_bytes(), prof_before)
            self.assertEqual(installer.read_manifest(prefix)["mode"], "lite")

    def test_install_corrupt_manifest_fails_clean(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            w1_before = (tr / "bin" / t1.binary).read_bytes()
            snap_before = _snapshot(tr)
            mp = installer.manifest_path(prefix)
            mp.write_bytes(b"not json {{{")
            bad = mp.read_bytes()
            with self.assertRaises(installer.InstallError):
                installer.install(t2, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            self.assertEqual((tr / "bin" / t1.binary).read_bytes(), w1_before)
            self.assertEqual(mp.read_bytes(), bad)
            self.assertFalse((tr / "bin" / t2.binary).exists())
            self.assertFalse((tr / t2.id).exists())

    def test_same_mode_second_install_leaves_other_wrappers_untouched(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            installer.install(t2, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            w2_before = (tr / "bin" / t2.binary).read_bytes()
            env_before = (tr / "env.sh").read_bytes()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            self.assertEqual((tr / "bin" / t2.binary).read_bytes(), w2_before)
            self.assertEqual((tr / "env.sh").read_bytes(), env_before)
            cur = installer.read_manifest(prefix)
            self.assertIn(t1.id, cur["tools"])
            self.assertIn(t2.id, cur["tools"])
            self.assertEqual(cur["mode"], "lite")

    def test_same_mode_python_change_regenerates_other_wrappers(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            w1_before = (tr / "bin" / t1.binary).read_text(encoding="utf-8")
            self.assertIn("python3", w1_before)
            installer.install(t2, prefix=prefix, mode="lite", machine="x86_64", home=home, python="otherpy", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            cur = installer.read_manifest(prefix)
            self.assertEqual(cur["python"], "otherpy")
            sb1 = tr / t1.id / "home"
            rb1 = sb1 / ".local" / "bin" / t1.binary
            exp1 = launcher.render_wrapper(t1, rb1, sb1, tr / "run", "lite", "otherpy", Path("/fake/src"))
            self.assertEqual((tr / "bin" / t1.binary).read_text(encoding="utf-8"), exp1)
            self.assertNotEqual((tr / "bin" / t1.binary).read_text(encoding="utf-8"), w1_before)

    def test_same_mode_heals_stale_other_wrapper(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            installer.install(t2, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            w1 = tr / "bin" / t1.binary
            w1.write_text("stale-wrapper\n", encoding="utf-8")
            installer.install(t2, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            sb1 = tr / t1.id / "home"
            rb1 = sb1 / ".local" / "bin" / t1.binary
            exp1 = launcher.render_wrapper(t1, rb1, sb1, tr / "run", "lite", "python3", Path("/fake/src"))
            self.assertEqual(w1.read_text(encoding="utf-8"), exp1)

    def test_uninstall_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            out = installer.uninstall(prefix)
            self.assertEqual(out, ["nothing to remove"])
            out2 = installer.uninstall(prefix, dry_run=True)
            self.assertEqual(out2, ["nothing to remove"])

    def test_default_fetch_rejects_non_https(self):
        with self.assertRaises(installer.InstallError):
            installer.default_fetch("http://example.com/x.sh")
        with self.assertRaises(installer.InstallError):
            installer.default_fetch("https://example.com/bad url")
        with mock.patch.object(urllib.request.OpenerDirector, "open") as m:
            m.side_effect = AssertionError("should not be called")
            with self.assertRaises(installer.InstallError):
                installer.default_fetch("http://example.com/nope")

    def test_default_fetch_rejects_http_final_url(self):
        """http final url is rejected"""
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b"payload"

            def geturl(self):
                return "http://x"

        with mock.patch.object(urllib.request.OpenerDirector, "open", return_value=FakeResp()):
            with self.assertRaises(installer.InstallError):
                installer.default_fetch("https://example.com/install.sh")

    def test_default_fetch_keeps_body_on_https_final_url(self):
        """https final url returns body"""
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b"payload-ok"

            def geturl(self):
                return "https://example.com/final.sh"

        with mock.patch.object(urllib.request.OpenerDirector, "open", return_value=FakeResp()):
            data = installer.default_fetch("https://example.com/install.sh")
            self.assertEqual(data, b"payload-ok")

    def test_default_fetch_oversize(self):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b"x" * (n)

        with mock.patch.object(urllib.request.OpenerDirector, "open", return_value=FakeResp()):
            with self.assertRaises(installer.InstallError) as cm:
                installer.default_fetch("https://example.com/big.sh", max_bytes=10)
            self.assertIn("too large", str(cm.exception))

    def test_default_fetch_ok(self):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b"hello"

        with mock.patch.object(urllib.request.OpenerDirector, "open", return_value=FakeResp()):
            data = installer.default_fetch("https://example.com/ok.sh", max_bytes=100)
            self.assertEqual(data, b"hello")

    def test_snapshot_after_uninstall_equals_original(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            root = td / "root"
            root.mkdir()
            prefix = root / "prefix"
            prefix.mkdir(parents=True, exist_ok=True)
            sentinel = prefix / "sentinel.txt"
            sentinel.write_text("sentinel\n", encoding="utf-8")
            sentinel.chmod(0o640)
            sentinel_bytes = sentinel.read_bytes()
            sentinel_mode = stat.S_IMODE(sentinel.stat().st_mode)
            home = root / "home"
            home.mkdir()
            profile = home / ".profile"
            profile.write_text("orig-profile\n", encoding="utf-8")
            profile.chmod(0o640)
            profile_bytes = profile.read_bytes()
            profile_mode = stat.S_IMODE(profile.stat().st_mode)
            orig = _snapshot(root)
            runner = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(),
                runner=runner,
                profiles=[profile],
            )
            after_install = _snapshot(root)
            self.assertTrue(len(after_install) > len(orig))
            self.assertTrue(any("opos-tools" in p for p in after_install))
            self.assertTrue(any(".profile" in p for p in after_install))
            installer.uninstall(prefix)
            after_uninstall = _snapshot(root)
            self.assertEqual(after_uninstall, orig)
            self.assertTrue(profile.exists())
            self.assertEqual(profile.read_bytes(), profile_bytes)
            if sys.platform != "win32":
                self.assertEqual(stat.S_IMODE(profile.stat().st_mode), profile_mode)
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_bytes(), sentinel_bytes)
            if sys.platform != "win32":
                self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), sentinel_mode)

    def test_uninstall_dry_run_changes_nothing(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            profile = home / "prof"
            runner = _runner_for({t.id: t.binary})
            installer.install(
                t,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(),
                runner=runner,
                profiles=[profile],
            )
            snap_before = _snapshot(prefix.parent)
            out = installer.uninstall(prefix, dry_run=True)
            self.assertTrue(any("would remove" in s for s in out))
            snap_after = _snapshot(prefix.parent)
            self.assertEqual(snap_before, snap_after)
            self.assertTrue(installer.tools_root(prefix).exists())

    def test_failed_first_install_leaves_no_tools_dir(self):
        """first failure cleans opos-tools"""
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            prefix.mkdir()
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t.id: t.binary}, fail=True)
            with self.assertRaises(installer.InstallError):
                installer.install(
                    t,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=_fetch_ok(),
                    runner=runner,
                    profiles=[],
                )
            self.assertTrue(prefix.is_dir())
            self.assertFalse(installer.tools_root(prefix).exists())
            self.assertFalse(installer.manifest_path(prefix).exists())
            self.assertEqual(_snapshot(prefix), set())
            self.assertEqual(list(prefix.iterdir()), [])

    def test_failed_second_install_keeps_first_tool_intact(self):
        """second failure keeps first tool"""
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            ok_runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            installer.install(
                t1,
                prefix=prefix,
                mode="lite",
                machine="x86_64",
                home=home,
                python="python3",
                src_dir=Path("/fake/src"),
                fetch=_fetch_ok(),
                runner=ok_runner,
                profiles=[],
            )
            manifest_before = installer.read_manifest(prefix)
            bin_before = _snapshot(installer.tools_root(prefix) / "bin")
            run_before = _snapshot(installer.tools_root(prefix) / "run")
            t1_before = _snapshot(installer.tools_root(prefix) / t1.id)
            wrapper_before = (installer.tools_root(prefix) / "bin" / t1.binary).read_text(encoding="utf-8")
            env_before = (installer.tools_root(prefix) / "env.sh").read_text(encoding="utf-8")
            fail_runner = _runner_for({t2.id: t2.binary}, fail=True)
            with self.assertRaises(installer.InstallError):
                installer.install(
                    t2,
                    prefix=prefix,
                    mode="lite",
                    machine="x86_64",
                    home=home,
                    python="python3",
                    src_dir=Path("/fake/src"),
                    fetch=_fetch_ok(),
                    runner=fail_runner,
                    profiles=[],
                )
            self.assertTrue(prefix.is_dir())
            self.assertTrue(installer.tools_root(prefix).is_dir())
            self.assertTrue((installer.tools_root(prefix) / t1.id).is_dir())
            self.assertEqual(_snapshot(installer.tools_root(prefix) / t1.id), t1_before)
            self.assertTrue((installer.tools_root(prefix) / t1.id / "home" / ".local" / "bin" / t1.binary).is_file())
            self.assertEqual(_snapshot(installer.tools_root(prefix) / "bin"), bin_before)
            self.assertEqual((installer.tools_root(prefix) / "bin" / t1.binary).read_text(encoding="utf-8"), wrapper_before)
            self.assertFalse((installer.tools_root(prefix) / "bin" / t2.binary).exists())
            self.assertEqual(_snapshot(installer.tools_root(prefix) / "run"), run_before)
            self.assertTrue((installer.tools_root(prefix) / "run").is_dir())
            self.assertEqual((installer.tools_root(prefix) / "env.sh").read_text(encoding="utf-8"), env_before)
            self.assertTrue(installer.manifest_path(prefix).is_file())
            cur = installer.read_manifest(prefix)
            self.assertEqual(cur, manifest_before)
            self.assertIn(t1.id, cur.get("tools", {}))
            self.assertNotIn(t2.id, cur.get("tools", {}))
            self.assertFalse((installer.tools_root(prefix) / t2.id).exists())

    def test_short_write_fully_buffered(self):
        """short writes do not truncate installer"""
        data = b"#!/bin/sh\necho hello\n" * 500
        t = _tool(installer_sha256=_sha(data))
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            seen = []
            def runner_ok(argv, env, timeout_s):
                seen.append(Path(argv[1]).read_bytes())
                p = Path(env["HOME"]) / ".local" / "bin" / t.binary
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("#!/bin/sh\necho hi\n")
                p.chmod(0o755)
                return 0, "ok"
            installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=lambda url: data, runner=runner_ok, profiles=[])
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0], data)
            self.assertTrue(installer.manifest_path(prefix).exists())
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            seen2 = []
            def runner2(argv, env, timeout_s):
                seen2.append(Path(argv[1]).read_bytes())
                p = Path(env["HOME"]) / ".local" / "bin" / t.binary
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("#!/bin/sh\necho hi\n")
                p.chmod(0o755)
                return 0, "ok"
            real_write = installer.os.write
            def short_write(fd, b):
                return real_write(fd, b[:7])
            with mock.patch.object(installer.os, "write", side_effect=short_write):
                installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=lambda url: data, runner=runner2, profiles=[])
            self.assertEqual(len(seen2), 1)
            self.assertEqual(seen2[0], data)
            self.assertTrue((installer.tools_root(prefix) / "bin" / t.binary).exists())
            self.assertTrue(installer.manifest_path(prefix).exists())

    def test_mid_write_failure_never_executes(self):
        """mid-write failure must not execute truncated installer"""
        data = b"#!/bin/sh\necho hello\n" * 500
        t = _tool(installer_sha256=_sha(data))
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            called = []
            def runner_ok(argv, env, timeout_s):
                called.append(list(argv))
                p = Path(env["HOME"]) / ".local" / "bin" / t.binary
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("#!/bin/sh\necho hi\n")
                p.chmod(0o755)
                return 0, "ok"
            installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=lambda url: data, runner=runner_ok, profiles=[])
            self.assertEqual(len(called), 1)
            self.assertTrue((installer.tools_root(prefix) / t.id / "home" / ".local" / "bin" / t.binary).exists())
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner_calls = []
            def runner_fail(argv, env, timeout_s):
                runner_calls.append(list(argv))
                p = Path(env["HOME"]) / ".local" / "bin" / t.binary
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("#!/bin/sh\necho hi\n")
                p.chmod(0o755)
                return 0, "ok"
            real_write = installer.os.write
            real_close = installer.os.close
            close_calls = []
            def tracking_close(fd):
                close_calls.append(fd)
                return real_close(fd)
            count = {"n": 0}
            def failing_write(fd, b):
                count["n"] += 1
                if count["n"] == 1:
                    return real_write(fd, b[:5])
                raise OSError("injected mid-write failure")
            with mock.patch.object(installer.os, "write", side_effect=failing_write):
                with mock.patch.object(installer.os, "close", side_effect=tracking_close):
                    with self.assertRaises(OSError):
                        installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=lambda url: data, runner=runner_fail, profiles=[])
            self.assertEqual(runner_calls, [])
            self.assertGreaterEqual(len(close_calls), 1)
            self.assertFalse(installer.manifest_path(prefix).exists())
            self.assertFalse((installer.tools_root(prefix) / t.id).exists())

    def test_default_fetch_refuses_redirect_to_http(self):
        """a redirect to plain http is reported, not followed"""
        import urllib.error

        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 302, "redirect blocked", {"Location": "http://evil.example/"}, None)

        with self.assertRaises(installer.InstallError) as cm:
            installer.default_fetch("https://example.com/start", opener=opener)
        self.assertIn("redirect not followed", str(cm.exception))
        self.assertIn("http://evil.example/", str(cm.exception))

    def test_default_fetch_refuses_https_redirect_to_other_host(self):
        """an https redirect to another host is not followed either (it was before)"""
        import urllib.error

        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 302, "redirect blocked", {"Location": "https://good.com/next"}, None)

        with self.assertRaisesRegex(installer.InstallError, "redirect not followed"):
            installer.default_fetch("https://example.com/start", opener=opener)

    def test_default_fetch_redirect_policy_real_opener(self):
        import io
        import urllib.response
        from email.message import Message

        def make_resp(url, code, msg, body, location=None):
            headers = Message()
            if location is not None:
                headers["Location"] = location
            resp = urllib.response.addinfourl(io.BytesIO(body), headers, url, code)
            resp.code = code
            resp.msg = msg
            return resp

        with self.subTest(kind="https-to-http"):
            start = "https://example.com/start"
            dest = "http://example.com/plain"
            https_calls = []
            http_calls = []

            def fake_https_open(handler_self, req, *a, **k):
                url = req.get_full_url()
                https_calls.append(url)
                if url == start:
                    return make_resp(url, 302, "Found", b"", location=dest)
                return make_resp(url, 200, "OK", b"ok-https")

            def fake_http_open(handler_self, req, *a, **k):
                url = req.get_full_url()
                http_calls.append(url)
                return make_resp(url, 200, "OK", b"ok-https")

            with mock.patch.object(urllib.request.HTTPSHandler, "https_open", fake_https_open):
                with mock.patch.object(urllib.request.HTTPHandler, "http_open", fake_http_open):
                    with self.assertRaisesRegex(installer.InstallError, "redirect not followed"):
                        installer.default_fetch(start)
            self.assertEqual(https_calls, [start])
            self.assertEqual(http_calls, [])
            with self.subTest("f253-http-spy-positive-control"):
                https_calls.clear()
                http_calls.clear()
                real_build = urllib.request.build_opener
                with mock.patch.object(urllib.request.HTTPSHandler, "https_open", fake_https_open):
                    with mock.patch.object(urllib.request.HTTPHandler, "http_open", fake_http_open):
                        # A bare opener follows the redirect, so the spy sees the http request;
                        # the final-address check still refuses the body.
                        with mock.patch.object(urllib.request, "build_opener", side_effect=lambda *a, **k: real_build()):
                            with self.assertRaisesRegex(installer.InstallError, "redirect not followed"):
                                installer.default_fetch(start)
                self.assertEqual(https_calls, [start])
                self.assertEqual(http_calls, [dest])

        with self.subTest(kind="https-to-http-to-https"):
            start = "https://example.com/start"
            middle = "http://example.com/middle"
            final = "https://example.com/final"
            https_calls = []
            http_calls = []

            def fake_https_open(handler_self, req, *a, **k):
                url = req.get_full_url()
                https_calls.append(url)
                if url == start:
                    return make_resp(url, 302, "Found", b"", location=middle)
                return make_resp(url, 200, "OK", b"ok-https")

            def fake_http_open(handler_self, req, *a, **k):
                url = req.get_full_url()
                http_calls.append(url)
                if url == middle:
                    return make_resp(url, 302, "Found", b"", location=final)
                return make_resp(url, 200, "OK", b"ok-https")

            with mock.patch.object(urllib.request.HTTPSHandler, "https_open", fake_https_open):
                with mock.patch.object(urllib.request.HTTPHandler, "http_open", fake_http_open):
                    with self.assertRaisesRegex(installer.InstallError, "redirect not followed"):
                        installer.default_fetch(start)
            self.assertEqual(https_calls, [start])
            self.assertEqual(http_calls, [])

        with self.subTest(kind="https-to-https"):
            start = "https://example.com/start"
            dest = "https://example.com/next"
            https_calls = []
            http_calls = []

            def fake_https_open(handler_self, req, *a, **k):
                url = req.get_full_url()
                https_calls.append(url)
                if url == start:
                    return make_resp(url, 302, "Found", b"", location=dest)
                return make_resp(url, 200, "OK", b"ok-https")

            def fake_http_open(handler_self, req, *a, **k):
                url = req.get_full_url()
                http_calls.append(url)
                return make_resp(url, 200, "OK", b"ok-https")

            # Installers follow no redirects, not even https on the same host.
            with mock.patch.object(urllib.request.HTTPSHandler, "https_open", fake_https_open):
                with mock.patch.object(urllib.request.HTTPHandler, "http_open", fake_http_open):
                    with self.assertRaisesRegex(installer.InstallError, "redirect not followed"):
                        installer.default_fetch(start)
            self.assertEqual(https_calls, [start])
            self.assertEqual(http_calls, [])

    def test_default_fetch_redirect_policy_detects_handler_removal(self):
        import io
        import urllib.response
        from email.message import Message

        def make_resp(url, code, msg, body, location=None):
            headers = Message()
            if location is not None:
                headers["Location"] = location
            resp = urllib.response.addinfourl(io.BytesIO(body), headers, url, code)
            resp.code = code
            resp.msg = msg
            return resp

        start = "https://example.com/start"
        middle = "http://example.com/middle"
        final = "https://example.com/final"
        requested = []

        def fake_https_open(handler_self, req, *a, **k):
            url = req.get_full_url()
            requested.append(url)
            if url == start:
                return make_resp(url, 302, "Found", b"", location=middle)
            return make_resp(url, 200, "OK", b"ok-https")

        def fake_http_open(handler_self, req, *a, **k):
            url = req.get_full_url()
            requested.append(url)
            if url == middle:
                return make_resp(url, 302, "Found", b"", location=final)
            return make_resp(url, 200, "OK", b"ok-https")

        holder = installer if hasattr(installer, "build_opener") else urllib.request
        real_build = holder.build_opener

        def bare_build(*a, **k):
            return real_build()

        with mock.patch.object(urllib.request.HTTPSHandler, "https_open", fake_https_open):
            with mock.patch.object(urllib.request.HTTPHandler, "http_open", fake_http_open):
                with self.assertRaisesRegex(installer.InstallError, "redirect not followed"):
                    installer.default_fetch(start)
                self.assertEqual(requested, [start])
                requested.clear()
                with mock.patch.object(holder, "build_opener", side_effect=bare_build):
                    data = installer.default_fetch(start)
                self.assertEqual(data, b"ok-https")
                self.assertEqual(requested, [start, middle, final])

    def test_install_wrappers_atomic_on_disk_full(self):
        """wrappers survive disk full"""
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            w1_path = tr / "bin" / t1.binary
            w1_before = w1_path.read_bytes()
            mp = installer.manifest_path(prefix)
            mp_before = mp.read_bytes()
            orig_write_text = Path.write_text
            orig_write_bytes = Path.write_bytes

            def fake_write_text(self, data, encoding=None, errors=None, newline=None):
                if self.name in ("bina", ".bina.tmp"):
                    if self.name == "bina":
                        try:
                            orig_write_bytes(self, b"")
                        except Exception:
                            pass
                    raise OSError(28, "No space left on device")
                return orig_write_text(self, data, encoding=encoding, errors=errors)

            def fake_write_bytes(self, data):
                if self.name in ("bina", ".bina.tmp", "manifest.json", ".manifest.json.tmp"):
                    if self.name in ("bina", "manifest.json"):
                        try:
                            orig_write_bytes(self, b"")
                        except Exception:
                            pass
                    raise OSError(28, "No space left on device")
                return orig_write_bytes(self, data)

            with mock.patch.object(Path, "write_text", fake_write_text):
                with mock.patch.object(Path, "write_bytes", fake_write_bytes):
                    with self.assertRaises(installer.InstallError):
                        installer.install(t2, prefix=prefix, mode="full", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            self.assertEqual(w1_path.read_bytes(), w1_before)
            self.assertEqual(mp.read_bytes(), mp_before)
            self.assertNotEqual(w1_path.read_bytes(), b"")
            self.assertFalse((tr / "bin" / t2.binary).exists())

    def test_install_env_atomic_on_disk_full(self):
        """env file survives disk full"""
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t.id: t.binary})
            fetch = _fetch_ok()
            installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[])
            env_path = installer.tools_root(prefix) / "env.sh"
            env_before = env_path.read_bytes()
            wrapper_path = installer.tools_root(prefix) / "bin" / t.binary
            wrapper_before = wrapper_path.read_bytes()
            mp_before = installer.manifest_path(prefix).read_bytes()
            orig_write_text = Path.write_text
            orig_write_bytes = Path.write_bytes

            def fake_write_text(self, data, encoding=None, errors=None, newline=None):
                if self.name in ("env.sh", ".env.sh.tmp"):
                    if self.name == "env.sh":
                        try:
                            orig_write_bytes(self, b"")
                        except Exception:
                            pass
                    raise OSError(28, "No space left on device")
                return orig_write_text(self, data, encoding=encoding, errors=errors)

            def fake_write_bytes(self, data):
                if self.name in ("env.sh", ".env.sh.tmp", "manifest.json", ".manifest.json.tmp"):
                    if self.name in ("env.sh", "manifest.json"):
                        try:
                            orig_write_bytes(self, b"")
                        except Exception:
                            pass
                    raise OSError(28, "No space left on device")
                return orig_write_bytes(self, data)

            with mock.patch.object(Path, "write_text", fake_write_text):
                with mock.patch.object(Path, "write_bytes", fake_write_bytes):
                    with self.assertRaises(installer.InstallError):
                        installer.install(t, prefix=prefix, mode="full", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t])
            self.assertEqual(env_path.read_bytes(), env_before)
            self.assertEqual(wrapper_path.read_bytes(), wrapper_before)
            self.assertEqual(installer.manifest_path(prefix).read_bytes(), mp_before)

    def test_install_rollback_reports_failures(self):
        """rollback failures are reported"""
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            orig_write_text = Path.write_text
            orig_write_bytes = Path.write_bytes
            cnt = {"n": 0}

            def fake_write_text(self, data, encoding=None, errors=None, newline=None):
                if self.name in ("bina", "binb", ".bina.tmp", ".binb.tmp"):
                    cnt["n"] += 1
                    if cnt["n"] == 2:
                        if self.name in ("binb",):
                            try:
                                orig_write_bytes(self, b"")
                            except Exception:
                                pass
                        raise OSError(28, "No space left on device")
                return orig_write_text(self, data, encoding=encoding, errors=errors)

            def fake_write_bytes(self, data):
                if self.name in ("bina", ".bina.tmp"):
                    if self.name == "bina":
                        try:
                            orig_write_bytes(self, b"")
                        except Exception:
                            pass
                    raise OSError(28, "restore failed")
                return orig_write_bytes(self, data)

            with mock.patch.object(Path, "write_text", fake_write_text):
                with mock.patch.object(Path, "write_bytes", fake_write_bytes):
                    with self.assertRaises(installer.InstallError) as cm:
                        installer.install(t2, prefix=prefix, mode="full", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            msg = str(cm.exception).lower()
            self.assertIn("rollback", msg)
            self.assertNotEqual((installer.tools_root(prefix) / "bin" / "bina").read_bytes(), b"")

    def test_set_mode_wrappers_atomic_on_disk_full(self):
        """set_mode wrappers survive disk full"""
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t.id: t.binary})
            fetch = _fetch_ok()
            installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[])
            w_path = installer.tools_root(prefix) / "bin" / t.binary
            w_before = w_path.read_bytes()
            mp_before = installer.manifest_path(prefix).read_bytes()
            orig_write_text = Path.write_text
            orig_write_bytes = Path.write_bytes

            def fake_write_text(self, data, encoding=None, errors=None, newline=None):
                if self.name in (t.binary, f".{t.binary}.tmp"):
                    if self.name == t.binary:
                        try:
                            orig_write_bytes(self, b"")
                        except Exception:
                            pass
                    raise OSError(28, "No space left on device")
                return orig_write_text(self, data, encoding=encoding, errors=errors)

            def fake_write_bytes(self, data):
                if self.name in (t.binary, f".{t.binary}.tmp", "manifest.json", ".manifest.json.tmp"):
                    if self.name in (t.binary, "manifest.json"):
                        try:
                            orig_write_bytes(self, b"")
                        except Exception:
                            pass
                    raise OSError(28, "No space left on device")
                return orig_write_bytes(self, data)

            with mock.patch.object(Path, "write_text", fake_write_text):
                with mock.patch.object(Path, "write_bytes", fake_write_bytes):
                    with self.assertRaises(installer.InstallError):
                        installer.set_mode(prefix, "full", [t])
            self.assertEqual(w_path.read_bytes(), w_before)
            self.assertEqual(installer.manifest_path(prefix).read_bytes(), mp_before)

    def test_install_backup_unreadable_aborts_before_write_preserves_existing(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            snap_before = _snapshot(tr)
            w1_path = tr / "bin" / t1.binary
            w1_before = w1_path.read_bytes()
            mp_path = installer.manifest_path(prefix)
            mp_before = mp_path.read_bytes()
            env_path = tr / "env.sh"
            env_before = env_path.read_bytes()
            orig_read_bytes = Path.read_bytes

            def failing_read(self, *args, **kwargs):
                if self == w1_path:
                    raise OSError("injected read failure")
                return orig_read_bytes(self, *args, **kwargs)

            with mock.patch.object(Path, "read_bytes", failing_read):
                with self.assertRaises(installer.InstallError):
                    installer.install(t2, prefix=prefix, mode="full", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            self.assertEqual(w1_path.read_bytes(), w1_before)
            self.assertEqual(mp_path.read_bytes(), mp_before)
            self.assertEqual(env_path.read_bytes(), env_before)
            self.assertEqual(_snapshot(tr), snap_before)
            self.assertFalse((tr / t2.id).exists())
            self.assertFalse((tr / "bin" / t2.binary).exists())

    def test_set_mode_backup_unreadable_aborts_preserves_existing(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t.id: t.binary})
            installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=_fetch_ok(), runner=runner, profiles=[])
            tr = installer.tools_root(prefix)
            w_path = tr / "bin" / t.binary
            w_before = w_path.read_bytes()
            mp_path = installer.manifest_path(prefix)
            mp_before = mp_path.read_bytes()
            orig = Path.read_bytes

            def failing(self, *a, **kw):
                if self == w_path:
                    raise OSError("injected")
                return orig(self, *a, **kw)

            with mock.patch.object(Path, "read_bytes", failing):
                with self.assertRaises(installer.InstallError):
                    installer.set_mode(prefix, "full", [t])
            self.assertEqual(w_path.read_bytes(), w_before)
            self.assertEqual(mp_path.read_bytes(), mp_before)

    def test_set_mode_skips_stale_entries_and_names_all(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary})
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=_fetch_ok(), runner=runner, profiles=[])
            mp = installer.manifest_path(prefix)
            cur = json.loads(mp.read_text(encoding="utf-8"))
            tr = installer.tools_root(prefix)
            stale_ids = ["".join(p) for p in (("stale", "-a"), ("stale", "-b"))]
            for sid in stale_ids:
                cur["tools"][sid] = {"binary": "bin-" + sid, "sandbox": str(tr / sid / "home"), "signin": "login"}
            mp.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            w_before = (tr / "bin" / t1.binary).read_bytes()
            out = installer.set_mode(prefix, "full", [t1])
            self.assertEqual(out[0], "mode set to full")
            for sid in stale_ids:
                self.assertIn(f"missing catalog entry: {sid} (wrapper left unchanged; restore this tool's original catalog entry, including installer_url, then rerun the mode command)", out)
            self.assertNotEqual((tr / "bin" / t1.binary).read_bytes(), w_before)
            self.assertEqual(installer.read_manifest(prefix)["mode"], "full")
            self.assertIn(t1.id, installer.read_manifest(prefix)["tools"])
            for sid in stale_ids:
                self.assertIn(sid, installer.read_manifest(prefix)["tools"])

    def test_set_mode_skips_real_stale_tool(self):
        t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
        t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
            fetch = _fetch_ok()
            installer.install(t1, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            installer.install(t2, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
            tr = installer.tools_root(prefix)
            w2_before = (tr / "bin" / t2.binary).read_bytes()
            snap_t2_before = _snapshot(tr / t2.id)
            row_before = dict(installer.read_manifest(prefix)["tools"][t2.id])
            out = installer.set_mode(prefix, "full", [t1])
            self.assertEqual(out[0], "mode set to full")
            self.assertIn(f"missing catalog entry: {t2.id} (wrapper left unchanged; restore this tool's original catalog entry, including installer_url, then rerun the mode command)", out)
            self.assertEqual((tr / "bin" / t2.binary).read_bytes(), w2_before)
            cur = installer.read_manifest(prefix)
            self.assertEqual(cur["mode"], "full")
            self.assertIn(t2.id, cur["tools"])
            self.assertEqual(dict(cur["tools"][t2.id]), row_before)
            self.assertEqual(_snapshot(tr / t2.id), snap_t2_before)

    def test_set_mode_bad_manifest_entry(self):
        t = _tool()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=_fetch_ok(), runner=_runner_for({t.id: t.binary}), profiles=[])
            mp = installer.manifest_path(prefix)
            cur = json.loads(mp.read_text(encoding="utf-8"))
            cur["tools"][t.id] = "not-a-dict"
            mp.write_text(json.dumps(cur) + "\n", encoding="utf-8")
            with self.assertRaises(installer.InstallError) as cm:
                installer.set_mode(prefix, "full", [t])
            self.assertIn(f"bad manifest entry: {t.id}", str(cm.exception))
            self.assertEqual(installer.read_manifest(prefix)["mode"], "lite")

    def test_set_mode_missing_catalog_advice_is_actionable(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            t = _tool()
            prefix = td / "prefix"
            home = td / "home"
            home.mkdir()
            installer.install(t, prefix=prefix, mode="lite", machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=_fetch_ok(), runner=_runner_for({t.id: t.binary}), profiles=[])
            wrapper_before = (installer.tools_root(prefix) / "bin" / t.binary).read_bytes()
            entry_before = dict(installer.read_manifest(prefix)["tools"][t.id])
            base = installer.tools_root(prefix) / t.id
            files_before = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}
            self.assertTrue(files_before)
            out = installer.set_mode(prefix, "full", [])
            want = f"missing catalog entry: {t.id} (wrapper left unchanged; restore this tool's original catalog entry, including installer_url, then rerun the mode command)"
            self.assertEqual(out, ["mode set to full", want])
            cur = installer.read_manifest(prefix)
            self.assertEqual(cur["mode"], "full")
            self.assertEqual((installer.tools_root(prefix) / "bin" / t.binary).read_bytes(), wrapper_before)
            files_after = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}
            self.assertEqual(files_after, files_before)
            self.assertEqual(dict(cur["tools"][t.id]), entry_before)

    def test_set_mode_missing_catalog_preserves_stale_wrapper(self):
        cases = [("lite", "full"), ("full", "lite")]
        for orig_mode, target_mode in cases:
            with self.subTest(orig=orig_mode, target=target_mode):
                t1 = _tool(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
                t2 = _tool(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")
                with tempfile.TemporaryDirectory() as td:
                    td = Path(td)
                    prefix = td / "prefix"
                    home = td / "home"
                    home.mkdir()
                    runner = _runner_for({t1.id: t1.binary, t2.id: t2.binary})
                    fetch = _fetch_ok()
                    installer.install(t1, prefix=prefix, mode=orig_mode, machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
                    installer.install(t2, prefix=prefix, mode=orig_mode, machine="x86_64", home=home, python="python3", src_dir=Path("/fake/src"), fetch=fetch, runner=runner, profiles=[], all_tools=[t1, t2])
                    tr = installer.tools_root(prefix)
                    w2_before = (tr / "bin" / t2.binary).read_bytes()
                    out = installer.set_mode(prefix, target_mode, [t1])
                    self.assertEqual(out[0], f"mode set to {target_mode}")
                    self.assertIn(f"missing catalog entry: {t2.id} (wrapper left unchanged; restore this tool's original catalog entry, including installer_url, then rerun the mode command)", out)
                    self.assertEqual((tr / "bin" / t2.binary).read_bytes(), w2_before)
                    self.assertEqual(installer.read_manifest(prefix)["mode"], target_mode)
                    self.assertIn(t2.id, installer.read_manifest(prefix)["tools"])
