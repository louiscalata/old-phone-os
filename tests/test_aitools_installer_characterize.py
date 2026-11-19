# SPDX-License-Identifier: GPL-3.0-or-later
"""Characterization tests for installer.install(): exact messages, check order, cleanup and rollback.

These pin the behaviour of install() as it stood before it was split into helpers (LOU-84), so the split
can be checked against them.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opos.aitools import installer, launcher
from opos.aitools.catalog import Tool

SRC = Path("/fake/src")
SCRIPT = b"echo hi"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tool(**over) -> Tool:
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
        installer_sha256=_sha(SCRIPT),
    )
    base.update(over)
    return Tool(**base)  # type: ignore[arg-type]


TOOL_A = dict(id="tool-a", binary="bina", installer_url="https://example.com/a.sh")
TOOL_B = dict(id="tool-b", binary="binb", installer_url="https://example.com/b.sh")


class Fetch:
    def __init__(self, data: bytes = SCRIPT, exc: BaseException | None = None) -> None:
        self.data = data
        self.exc = exc
        self.calls: list[str] = []

    def __call__(self, url):
        self.calls.append(url)
        if self.exc is not None:
            raise self.exc
        return self.data


class Runner:
    """Records each call; creates the tool binary under HOME unless told not to."""

    def __init__(self, rc: int = 0, out: str = "ok", create: bool = True) -> None:
        self.rc = rc
        self.out = out
        self.create = create
        self.calls: list[tuple[list[str], dict[str, str], float]] = []
        self.scripts: list[bytes] = []

    def __call__(self, argv, env, timeout_s):
        self.calls.append((list(argv), dict(env), timeout_s))
        self.scripts.append(Path(argv[1]).read_bytes())
        if self.rc == 0 and self.create:
            home = Path(env["HOME"])
            binary = {"tool-a": "bina", "tool-b": "binb"}.get(home.parent.name, "testbin")
            p = home / ".local" / "bin" / binary
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            p.chmod(0o755)
        return self.rc, self.out


class InstallCharacterization(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.prefix = self.base / "prefix"
        self.home = self.base / "home"
        self.home.mkdir()
        self.tr = installer.tools_root(self.prefix)

    def install(self, tool, fetch=None, runner=None, **kw):
        kw.setdefault("mode", "lite")
        kw.setdefault("profiles", [])
        kw.setdefault("free_mb_fn", lambda p: 100000)
        return installer.install(
            tool,
            prefix=self.prefix,
            machine="x86_64",
            home=self.home,
            python="python3",
            src_dir=SRC,
            fetch=fetch if fetch is not None else Fetch(),
            runner=runner if runner is not None else Runner(),
            **kw,
        )

    # --- order of the up-front checks -------------------------------------------------------------

    def test_unknown_mode_is_checked_first(self):
        fetch = Fetch()
        bad = _tool(archs=("aarch64",), installer_sha256="")
        with self.assertRaises(installer.InstallError) as cm:
            self.install(bad, fetch=fetch, mode="bogus", free_mb_fn=lambda p: 0)
        self.assertEqual(str(cm.exception), "unknown mode: bogus")
        self.assertEqual(fetch.calls, [])
        self.assertFalse(self.prefix.exists())

    def test_preflight_problems_joined_and_checked_before_digest(self):
        bad = _tool(archs=("aarch64",), min_free_mb=100, installer_sha256="")
        with self.assertRaises(installer.InstallError) as cm:
            self.install(bad, free_mb_fn=lambda p: 1)
        self.assertEqual(
            str(cm.exception), "unsupported CPU arch 'x86_64' for this tool; only 1 MB free, 100 MB required"
        )

    def test_digest_problem_messages_exact(self):
        t = _tool(installer_sha256="")
        with self.assertRaises(installer.InstallError) as cm:
            self.install(t)
        self.assertEqual(
            str(cm.exception),
            f"no installer_sha256 in the catalog for {t.installer_url}; OPOS runs a downloaded installer only "
            "after checking its sha256. Nothing was downloaded.",
        )
        t = _tool(installer_sha256=_sha(SCRIPT).upper())
        with self.assertRaises(installer.InstallError) as cm:
            self.install(t)
        self.assertEqual(
            str(cm.exception),
            f"installer_sha256 for {t.id} is not 64 lowercase hex characters. Nothing was downloaded.",
        )

    def test_digest_problem_checked_before_manifest_is_read(self):
        mp = installer.manifest_path(self.prefix)
        mp.parent.mkdir(parents=True)
        mp.write_bytes(b"not json {{{")
        with self.assertRaisesRegex(installer.InstallError, "^no installer_sha256"):
            self.install(_tool(installer_sha256=""))

    def test_corrupt_manifest_refused_even_on_dry_run(self):
        mp = installer.manifest_path(self.prefix)
        mp.parent.mkdir(parents=True)
        mp.write_bytes(b"not json {{{")
        try:
            json.loads("not json {{{")
        except ValueError as e:
            want = str(e)
        fetch = Fetch()
        with self.assertRaises(installer.InstallError) as cm:
            self.install(_tool(), fetch=fetch, dry_run=True)
        self.assertEqual(str(cm.exception), want)
        self.assertEqual(fetch.calls, [])

    def test_missing_catalog_entry_refused_before_dry_run_returns(self):
        self.install(_tool(**TOOL_A))
        mp_before = installer.manifest_path(self.prefix).read_bytes()
        fetch = Fetch()
        t2 = _tool(**TOOL_B)
        with self.assertRaises(installer.InstallError) as cm:
            self.install(t2, fetch=fetch, mode="full", all_tools=[t2], dry_run=True)
        self.assertEqual(str(cm.exception), "missing catalog entry: tool-a")
        self.assertEqual(fetch.calls, [])
        self.assertEqual(installer.manifest_path(self.prefix).read_bytes(), mp_before)
        self.assertFalse((self.tr / "tool-b").exists())

    # --- return values ----------------------------------------------------------------------------

    def test_dry_run_lines_exact(self):
        t = _tool()
        out = self.install(t, dry_run=True)
        self.assertEqual(
            out,
            [
                f"would install {t.id} to {self.prefix}",
                f"would fetch {t.installer_url} and run it only if its sha256 matches",
                f"Sign in yourself: {t.signin}",
            ],
        )
        self.assertFalse(self.tr.exists())

    def test_success_lines_exact(self):
        t = _tool()
        out = self.install(t)
        self.assertEqual(
            out,
            [
                f"installed {t.id} to {self.prefix}",
                f"wrapper {self.tr / 'bin' / t.binary}",
                f"Sign in yourself: {t.signin}",
            ],
        )

    # --- download, write, digest, run -------------------------------------------------------------

    def test_runner_call_shape(self):
        t = _tool(installer_args=("--yes", "-q"), installer_env={"FOO": "bar", "TERM": "xterm"})
        runner = Runner()
        self.install(t, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        argv, env, timeout_s = runner.calls[0]
        self.assertEqual(argv[0], "sh")
        self.assertEqual(argv[2:], ["--yes", "-q"])
        self.assertEqual(Path(argv[1]).parent, self.tr / t.id)
        self.assertFalse(Path(argv[1]).exists())
        self.assertEqual(runner.scripts, [SCRIPT])
        self.assertEqual(timeout_s, 900.0)
        self.assertEqual(env["HOME"], str(self.tr / t.id / "home"))
        self.assertEqual(env["FOO"], "bar")
        self.assertEqual(env["TERM"], "xterm")
        self.assertEqual(env["LANG"], "C.UTF-8")

    def test_installer_failed_message_exact_and_fresh_cleanup(self):
        with self.assertRaises(installer.InstallError) as cm:
            self.install(_tool(), runner=Runner(rc=3, out="tail out"))
        self.assertEqual(str(cm.exception), "installer failed (3): tail out")
        self.assertFalse(self.tr.exists())
        self.assertTrue(self.prefix.is_dir())

    def test_missing_binary_message_exact(self):
        t = _tool()
        with self.assertRaises(installer.InstallError) as cm:
            self.install(t, runner=Runner(create=False))
        real_bin = self.tr / t.id / "home" / ".local" / "bin" / t.binary
        self.assertEqual(str(cm.exception), f"installer did not create {real_bin}")
        self.assertFalse(self.tr.exists())

    def test_digest_mismatch_message_exact(self):
        body = b"changed"
        for checked, when in (("2026-11-03", " (measured 2026-11-03)"), ("", "")):
            with self.subTest(checked=checked):
                t = _tool(installer_sha256_checked=checked)
                runner = Runner()
                with self.assertRaises(installer.InstallError) as cm:
                    self.install(t, fetch=Fetch(body), runner=runner)
                self.assertEqual(
                    str(cm.exception),
                    f"the vendor installer changed: {t.installer_url} now has sha256 {_sha(body)[:16]}..., "
                    f"the catalog lists {_sha(SCRIPT)[:16]}...{when}; nothing was run. Review the new script, "
                    "then update installer_sha256 in the catalog.",
                )
                self.assertEqual(runner.calls, [])
                self.assertFalse(self.tr.exists())

    def test_fetch_exception_propagates_unwrapped_and_fresh_dirs_removed(self):
        for exc in (ValueError("boom"), installer.InstallError("HTTP 404 from x")):
            with self.subTest(exc=type(exc).__name__):
                runner = Runner()
                with self.assertRaises(type(exc)) as cm:
                    self.install(_tool(), fetch=Fetch(exc=exc), runner=runner)
                self.assertIs(cm.exception, exc)
                self.assertEqual(runner.calls, [])
                self.assertFalse(self.tr.exists())

    def test_fetch_failure_with_existing_install_leaves_it_alone(self):
        t = _tool()
        self.install(t)
        mp_before = installer.manifest_path(self.prefix).read_bytes()
        wrapper_before = (self.tr / "bin" / t.binary).read_bytes()
        with self.assertRaises(ValueError):
            self.install(t, fetch=Fetch(exc=ValueError("boom")), force=True)
        self.assertEqual(installer.manifest_path(self.prefix).read_bytes(), mp_before)
        self.assertEqual((self.tr / "bin" / t.binary).read_bytes(), wrapper_before)
        self.assertTrue((self.tr / t.id / "home" / ".local" / "bin" / t.binary).is_file())

    def test_zero_length_write_raises_oserror_unwrapped(self):
        runner = Runner()
        with mock.patch.object(installer.os, "write", return_value=0):
            with self.assertRaises(OSError) as cm:
                self.install(_tool(), runner=runner)
        self.assertIs(type(cm.exception), OSError)
        self.assertEqual(str(cm.exception), "short write: incomplete installer")
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.tr.exists())

    # --- profiles ---------------------------------------------------------------------------------

    def test_default_profiles_and_merge(self):
        profile = self.home / ".profile"
        bashrc = self.home / ".bashrc"
        self.install(_tool(**TOOL_A), profiles=None)
        self.assertEqual(installer.read_manifest(self.prefix)["profiles"], [str(profile)])
        self.assertIn(launcher.BEGIN, profile.read_text(encoding="utf-8"))
        bashrc.write_text("# rc\n", encoding="utf-8")
        t1, t2 = _tool(**TOOL_A), _tool(**TOOL_B)
        self.install(t2, profiles=None, all_tools=[t1, t2])
        self.assertEqual(installer.read_manifest(self.prefix)["profiles"], [str(profile), str(bashrc)])

    def test_profile_error_propagates_unwrapped_after_download(self):
        t = _tool()
        with mock.patch.object(launcher, "add_profile_block", side_effect=RuntimeError("profile boom")):
            with self.assertRaises(RuntimeError) as cm:
                self.install(t, profiles=[self.home / ".profile"])
        self.assertIs(type(cm.exception), RuntimeError)
        self.assertTrue((self.tr / t.id / "home" / ".local" / "bin" / t.binary).is_file())
        self.assertFalse(installer.manifest_path(self.prefix).exists())
        self.assertFalse((self.tr / "bin" / t.binary).exists())

    # --- manifest and commit ----------------------------------------------------------------------

    def test_mode_change_updates_other_tool_entries(self):
        t1, t2 = _tool(**TOOL_A), _tool(**TOOL_B)
        self.install(t1)
        before = dict(installer.read_manifest(self.prefix)["tools"]["tool-a"])
        self.install(t2, mode="full", all_tools=[t1, t2])
        m = installer.read_manifest(self.prefix)
        self.assertEqual(m["mode"], "full")
        self.assertEqual(m["tools"]["tool-a"], {**before, "mode": "full"})
        self.assertEqual(
            m["tools"]["tool-b"],
            {"binary": "binb", "sandbox": str(self.tr / "tool-b" / "home"), "signin": t2.signin, "mode": "full"},
        )

    def test_commit_failure_fresh_message_cause_and_leftovers(self):
        t = _tool()
        err = OSError("disk full")
        with mock.patch.object(installer, "_atomic_manifest", side_effect=err):
            with self.assertRaises(installer.InstallError) as cm:
                self.install(t)
        self.assertEqual(str(cm.exception), "disk full")
        self.assertIs(cm.exception.__cause__, err)
        self.assertFalse((self.tr / t.id).exists())
        self.assertFalse((self.tr / "bin" / t.binary).exists())
        self.assertFalse((self.tr / "env.sh").exists())
        self.assertFalse(installer.manifest_path(self.prefix).exists())
        # Only the tool dir is removed after a commit failure; the shared dirs stay.
        self.assertTrue((self.tr / "bin").is_dir())
        self.assertTrue((self.tr / "run").is_dir())

    def test_rollback_errors_listed_in_write_order(self):
        t = _tool()
        self.install(t)
        wrapper = self.tr / "bin" / t.binary
        env_file = self.tr / "env.sh"
        with mock.patch.object(installer, "_atomic_manifest", side_effect=OSError("disk full")):
            with mock.patch.object(installer, "_atomic_write_bytes", side_effect=OSError("nope")):
                with self.assertRaises(installer.InstallError) as cm:
                    self.install(t, force=True)
        self.assertEqual(str(cm.exception), f"disk full (rollback failed: {wrapper}: nope; {env_file}: nope)")
        self.assertTrue((self.tr / t.id).is_dir())

    def test_rollback_order_puts_regenerated_wrappers_first(self):
        t1, t2 = _tool(**TOOL_A), _tool(**TOOL_B)
        self.install(t1)
        wa = self.tr / "bin" / "bina"
        wb = self.tr / "bin" / "binb"
        env_file = self.tr / "env.sh"
        with mock.patch.object(installer, "_atomic_manifest", side_effect=OSError("disk full")):
            with mock.patch.object(installer, "_atomic_write_bytes", side_effect=OSError("nope")):
                with self.assertRaises(installer.InstallError) as cm:
                    self.install(t2, mode="full", all_tools=[t1, t2])
        self.assertEqual(str(cm.exception), f"disk full (rollback failed: {wa}: nope; {env_file}: nope)")
        self.assertFalse(wb.exists())
        self.assertFalse((self.tr / "tool-b").exists())
        self.assertTrue((self.tr / "tool-a").is_dir())


if __name__ == "__main__":
    unittest.main()
