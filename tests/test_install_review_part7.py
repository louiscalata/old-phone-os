# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

if not __package__:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.install_review_support import _read_install_sh


class TestInstallReview(unittest.TestCase):
    def test_fallback_uses_only_trusted_manifest_paths(self):
        text = _read_install_sh()
        self.assertNotIn('"$OPOS_HOME"/*', text, "must not use prefix trust for launcher")
        self.assertNotIn('"$OPOS_BIN_DIR"/*', text, "must not use prefix trust for launcher")
        start = text.index("verified_recovery()")
        end = text.index("do_uninstall()", start)
        helper = text[start:end]
        self.assertRegex(
            helper,
            r'(?m)^\s*\[ "(\$\{UVENV:-\}|\$UVENV)" = "\$OPOS_HOME/venv" \]',
            "fallback must validate UVENV with strict equality",
        )
        self.assertRegex(
            helper,
            r'(?m)^\s*opos_owned_path "\$UVENV" "\$OPOS_HOME/venv"',
            "fallback must check trusted venv ownership",
        )
        self.assertRegex(helper, r'(?m)^\s*\[ -x "\$UVENV/bin/python" \]', "fallback must check recovery python")
        helper_start = text.index("run_verified_recovery()")
        helper_end = text.index("do_uninstall()", helper_start)
        helper_fn = text[helper_start:helper_end]
        anchor = text.index("do_uninstall()")
        body = text[anchor:]
        helper_sites = re.findall(r"(?m)^\s*if verified_recovery; then\s*\n\s*_verified=1", helper_fn)
        body_sites = re.findall(r"(?m)^\s*if verified_recovery; then\s*\n\s*_verified=1", body)
        self.assertEqual(len(helper_sites), 1, "execution helper must have one verification site")
        self.assertEqual(len(body_sites), 1, "do_uninstall must retain one dry-run verification site")
        self.assertEqual(
            len(helper_sites) + len(body_sites), 2, "must have two verification sites across the two functions"
        )
        self.assertNotRegex(body, r"(?<![A-Za-z0-9_])UH(?![A-Za-z0-9_])", "must not retain standalone UH cleanup")
        self.assertRegex(
            body,
            r'(?m)^\s*if \[ "\$LAUNCHER" = "\$DEF_LAUNCHER" \]; then\s*\n\s*remove_path "\$LAUNCHER"',
            "fallback must validate LAUNCHER with strict equality",
        )
        exec_lines = [
            line for line in helper_fn.splitlines() if "$UVENV/bin/python" in line and "-m opos.setup" in line
        ]
        self.assertEqual(len(exec_lines), 1, "execution helper must run one verified command")
        preview_lines = [
            line
            for line in body.splitlines()
            if "would run recovery entry point" in line and "$UVENV/bin/python" in line
        ]
        self.assertEqual(len(preview_lines), 1, "dry-run must preview one recovery entry point")
        for fragment, target in ((helper_fn, exec_lines[0]), (body, preview_lines[0])):
            split_lines = fragment.splitlines()
            use = next(i for i, line in enumerate(split_lines) if target in line)
            stack = []
            for line in split_lines[:use]:
                stripped = line.strip()
                if stripped.startswith("if ") or stripped.startswith("if\t"):
                    stack.append("_verified" in line and "-eq 1" in line)
                elif (
                    stripped == "else"
                    or stripped.startswith("else ")
                    or stripped.startswith("else;")
                    or stripped.startswith("else\t")
                    or stripped.startswith("elif ")
                    or stripped.startswith("elif\t")
                ):
                    if stack:
                        stack[-1] = False
                elif stack and (stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;")):
                    stack.pop()
            self.assertTrue(any(stack), "recovery python must stay inside verified recovery")

    def test_recovery_python_moved_outside_guard_is_rejected(self):
        text = _read_install_sh()
        self.test_fallback_uses_only_trusted_manifest_paths()
        marker = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        split = text.splitlines()
        targets = [
            i
            for i, line in enumerate(split)
            if ("$UVENV/bin/python" in line and "-m opos.setup" in line)
            or ("would run recovery entry point" in line and "$UVENV/bin/python" in line)
        ]
        self.assertEqual(len(targets), 2, "must target execution command and dry-run preview")
        for pos in range(len(targets)):
            with self.subTest(site=pos):
                idx = targets[pos]
                moving = split[idx]
                remaining = split[:idx] + split[idx + 1 :]
                at = next(i for i, line in enumerate(remaining) if marker in line)
                mutant = "\n".join(remaining[:at] + [moving] + remaining[at:]) + "\n"
                self.assertNotEqual(mutant, text)
                with unittest.mock.patch(__name__ + "._read_install_sh", return_value=mutant):
                    with self.assertRaises(AssertionError):
                        self.test_fallback_uses_only_trusted_manifest_paths()

    def test_recovery_python_in_unverified_branch_is_rejected(self):
        text = _read_install_sh()
        self.test_fallback_uses_only_trusted_manifest_paths()
        split = text.splitlines()
        hits = [
            i
            for i, line in enumerate(split)
            if "would run recovery entry point" in line and "$UVENV/bin/python" in line
        ]
        self.assertEqual(len(hits), 1, "must find one dry-run preview")
        idx = hits[0]
        self.assertEqual(split[idx + 1].strip(), "else", "preview must be followed by else")
        branches = ["else", "elif true; then", "elif\ttrue; then"]
        for branch in branches:
            with self.subTest(branch=branch):
                lines = list(split)
                preview = lines[idx]
                indent = preview[: len(preview) - len(preview.lstrip())]
                lines[idx] = indent + ":"
                nxt = lines[idx + 1]
                nindent = nxt[: len(nxt) - len(nxt.lstrip())]
                lines[idx + 1] = nindent + branch
                lines.insert(idx + 2, preview)
                mutant = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
                self.assertNotEqual(mutant, text)
                with unittest.mock.patch(__name__ + "._read_install_sh", return_value=mutant):
                    with self.assertRaisesRegex(AssertionError, "recovery python must stay inside verified recovery"):
                        self.test_fallback_uses_only_trusted_manifest_paths()

    def test_each_manifest_destructive_op_has_enclosing_guard(self):
        text = _read_install_sh()
        self.test_fallback_uses_only_trusted_manifest_paths()
        recovery = "if verified_recovery; then"
        parts = text.split(recovery)
        self.assertEqual(len(parts), 3, "expected two recovery sites")
        for idx in range(2):
            with self.subTest(site="recovery_%d" % idx):
                fixture = parts[0]
                for j in range(2):
                    guard = "if true; then # " + recovery if j == idx else recovery
                    fixture += guard + parts[j + 1]
                self.assertNotEqual(fixture, text)
                with unittest.mock.patch(__name__ + "._read_install_sh", return_value=fixture):
                    with self.assertRaises(AssertionError):
                        self.test_fallback_uses_only_trusted_manifest_paths()
        lg = 'if [ "$LAUNCHER" = "$DEF_LAUNCHER" ]; then'
        lparts = text.split(lg)
        self.assertGreaterEqual(len(lparts), 3, "expected launcher guards")
        with self.subTest(site="launcher"):
            fixture = lparts[0]
            for j in range(len(lparts) - 1):
                guard = "if true; then # " + lg if j == 1 else lg
                fixture += guard + lparts[j + 1]
            self.assertNotEqual(fixture, text)
            with unittest.mock.patch(__name__ + "._read_install_sh", return_value=fixture):
                with self.assertRaises(AssertionError):
                    self.test_fallback_uses_only_trusted_manifest_paths()

    def test_fallback_rejects_reintroduced_uh(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall()")
        idx = text.index("FOUND=0", anchor)
        mutated = text[:idx] + 'UH="$OPOS_HOME"\n' + text[idx:]
        self.assertNotEqual(mutated, text, "mutation must reintroduce UH")
        with unittest.mock.patch(__name__ + "._read_install_sh", return_value=mutated):
            with self.assertRaises(AssertionError):
                self.test_fallback_uses_only_trusted_manifest_paths()

    def test_fallback_trust_checks_accept_guarded_uvenv(self):
        text = _read_install_sh()
        plain = '"$UVENV" = "$OPOS_HOME/venv"'
        guarded_operand = '"${UVENV:-}" = "$OPOS_HOME/venv"'
        fixture = text.replace(plain, guarded_operand)
        self.assertNotIn(plain, fixture, "fixture must be fully guarded")
        with unittest.mock.patch(__name__ + "._read_install_sh", return_value=fixture):
            self.test_fallback_uses_only_trusted_manifest_paths()
        prefix = '[ "${UVENV:-}" = "$OPOS_HOME/venv" ]'
        broken = fixture.replace(prefix, "", 1)
        self.assertNotEqual(broken, fixture, "must remove one guard")
        with unittest.mock.patch(__name__ + "._read_install_sh", return_value=broken):
            with self.assertRaises(AssertionError):
                self.test_fallback_uses_only_trusted_manifest_paths()

    def test_uninstall_fallback_survives_unset_uvenv(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall()")
        head = text[anchor : anchor + 400]
        self.assertRegex(head, r'do_uninstall\(\) \{\n\s*UVENV=""')

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_dry_run_removes_home_once(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        for name, use_external in (("internal", False), ("external", True)):
            with self.subTest(venv=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home_dir = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    internal_venv = opos_home / "venv"
                    external_venv = tmp_path / "external" / "opos" / "venv"
                    bindir = tmp_path / "bin"
                    opos_root = tmp_path / "root"
                    for p in (home_dir, internal_venv, external_venv, bindir, opos_root):
                        p.mkdir(parents=True)
                    internal_marker = internal_venv / "".join(["dum", "my"])
                    external_marker = external_venv / "".join(["dum", "my"])
                    internal_bytes = b"".join([b"internal-", b"ven", b"v-1"])
                    external_bytes = b"".join([b"external-", b"ven", b"v-2"])
                    internal_marker.write_bytes(internal_bytes)
                    external_marker.write_bytes(external_bytes)
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    selected = external_venv if use_external else internal_venv
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(selected) + "\nrepo=\n",
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["HOME"] = str(home_dir)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(opos_root)
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--dry-run"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    lines = proc.stdout.splitlines()
                    home_line = "==> would remove " + str(opos_home)
                    self.assertEqual(lines.count(home_line), 1, name)
                    self.assertNotIn("==> would remove " + str(internal_venv), lines, name)
                    self.assertNotIn("==> would remove " + str(external_venv), lines, name)
                    self.assertEqual(internal_marker.read_bytes(), internal_bytes, name)
                    self.assertEqual(external_marker.read_bytes(), external_bytes, name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_ignores_manifest_launcher(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            expected = bindir / "opos"
            expected.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            expected.chmod(expected.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            other = bindir / "unrelated"
            other.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            other.chmod(other.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(other) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(
                "==> would run " + str(expected) + " uninstall --dry-run",
                proc.stdout.splitlines(),
            )
            self.assertIn(str(other), proc.stderr)
            self.assertIn("warning", proc.stderr.lower())
            self.assertNotIn("would run " + str(other), proc.stdout + proc.stderr)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_rejects_replaced_recorded_launcher(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        for flags in ([], ["--force"], ["--dry-run"]):
            with self.subTest(flags=flags):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos = tmp_path / "opos"
                    venv = opos / "venv"
                    bindir = tmp_path / "bin"
                    root = tmp_path / "root"
                    for p in (home, venv, bindir, root):
                        p.mkdir(parents=True)
                    marker = tmp_path / "ran"
                    launcher = bindir / "opos"
                    launcher.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 0\n', encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    recorded = (
                        subprocess.run(["cksum"], input=launcher.read_bytes(), capture_output=True, check=True)
                        .stdout.decode()
                        .strip()
                    )
                    (opos / "manifest").write_text(
                        "launcher="
                        + str(launcher)
                        + "\nvenv="
                        + str(venv)
                        + "\nrepo=\nlauncher_cksum="
                        + recorded
                        + "\n",
                        encoding="utf-8",
                    )
                    launcher.write_text(
                        '#!/bin/sh\n# replaced\ntouch "' + str(marker) + '"\nexit 0\n', encoding="utf-8"
                    )
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    launcher_before = launcher.read_bytes()
                    manifest_before = (opos / "manifest").read_bytes()
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", *flags], capture_output=True, text=True, env=env, timeout=30
                    )
                    self.assertEqual(proc.returncode, 1)
                    self.assertIn("warning: ownership verification failed: " + str(launcher), proc.stderr)
                    self.assertFalse(marker.exists())
                    self.assertEqual(launcher.read_bytes(), launcher_before)
                    self.assertEqual((opos / "manifest").read_bytes(), manifest_before)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos = tmp_path / "opos"
            venv = opos / "venv"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            for p in (home, venv, bindir, root):
                p.mkdir(parents=True)
            marker = tmp_path / "ran"
            launcher = bindir / "opos"
            launcher.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 0\n', encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            recorded = (
                subprocess.run(["cksum"], input=launcher.read_bytes(), capture_output=True, check=True)
                .stdout.decode()
                .strip()
            )
            (opos / "manifest").write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv) + "\nrepo=\nlauncher_cksum=" + recorded + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"], capture_output=True, text=True, env=env, timeout=30
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("==> would run " + str(launcher) + " uninstall --dry-run", proc.stdout.splitlines())
            self.assertFalse(marker.exists())

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_manifest_launcher_execution_gate(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        cases = ["outside", "dotdot", "symlink", "directory", "default_launcher", "empty_opos_home"]
        for name in cases:
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = pathlib.Path(os.path.realpath(tmp))
                    home_dir = root / "home"
                    xdg = root / "xdg"
                    bindir = root / "bin"
                    opos_home = xdg / "opos" if name == "empty_opos_home" else root / "opos"
                    home_dir.mkdir(parents=True)
                    xdg.mkdir(parents=True)
                    bindir.mkdir(parents=True)
                    opos_home.mkdir(parents=True)
                    mal_marker = root / "malicious.marker"
                    def_marker = root / "default.marker"
                    outside = root / "outside"
                    if name == "directory":
                        launch_dir = root / "launcher-dir"
                        launch_dir.mkdir()
                        value = str(launch_dir)
                    else:
                        outside.write_text(
                            '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$OPOS_TEST_MAL_MARKER"\nexit 42\n',
                            encoding="utf-8",
                        )
                        outside.chmod(outside.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        if name == "dotdot":
                            value = str(opos_home) + "/../outside"
                        elif name == "symlink":
                            link = opos_home / "launcher-link"
                            link.symlink_to(outside)
                            value = str(link)
                        else:
                            value = str(outside)
                    if name == "default_launcher" or name == "empty_opos_home":
                        default = bindir / "opos"
                        default.write_text(
                            '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$OPOS_TEST_DEF_MARKER"\nexit 42\n',
                            encoding="utf-8",
                        )
                        default.chmod(default.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (opos_home / "manifest").write_text(
                        "launcher=" + value + "\nvenv=" + str(opos_home) + "/venv\nrepo=\n",
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["HOME"] = str(home_dir)
                    env["XDG_DATA_HOME"] = str(xdg)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_TEST_MAL_MARKER"] = str(mal_marker)
                    env["OPOS_TEST_DEF_MARKER"] = str(def_marker)
                    if name == "empty_opos_home":
                        env["OPOS_HOME"] = ""
                    else:
                        env["OPOS_HOME"] = str(opos_home)
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                    )
                    diag = proc.stdout + proc.stderr
                    if name == "default_launcher" or name == "empty_opos_home":
                        self.assertEqual(proc.returncode, 42, diag)
                        self.assertEqual(def_marker.read_text(encoding="utf-8"), "uninstall\n", diag)
                        self.assertFalse(mal_marker.exists(), diag)
                    else:
                        self.assertEqual(proc.returncode, 1, diag)
                        self.assertFalse(mal_marker.exists(), diag)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_repo_canonicalized_and_mismatch_reported(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def run(home, bindir, args):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"] + args,
                capture_output=True,
                text=True,
                env=env,
            )

        def make_recovery_only(home, repo_value):
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher="
                + str(home.parent / "bin" / "opos")
                + "\n"
                + "venv="
                + str(home / "venv")
                + "\n"
                + "repo="
                + repo_value
                + "\n",
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            make_recovery_only(home, str(root) + "/")
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            variant = str(root) + "/."
            make_recovery_only(home, variant)
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            link = pathlib.Path(tmp) / "link"
            link.symlink_to(root)
            make_recovery_only(home, str(link))
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            recorded = str(pathlib.Path(tmp) / "elsewhere")
            pathlib.Path(recorded).mkdir()
            make_recovery_only(home, recorded)
            proc = run(home, bindir, [])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("repo mismatch: run the uninstaller from the original checkout", proc.stderr.splitlines())
            self.assertNotIn(recorded, proc.stdout + proc.stderr)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_live_recovery_canonicalizes_repo_and_preserves_mismatches(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]
        resolved = os.path.realpath(str(root))
        cases = [
            ("launcher_absent_equivalent", False, True),
            ("launcher_absent_mismatch", False, False),
            ("launcher_fails_equivalent", True, True),
            ("launcher_fails_mismatch", True, False),
        ]
        for name, with_launcher, equivalent in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    elsewhere = tmp_path / "elsewhere"
                    for p in (home, opos, bindir, elsewhere):
                        p.mkdir()
                    recorded = resolved + "/" if equivalent else str(elsewhere)
                    launcher = bindir / "opos"
                    venv_python = opos / "venv" / "bin" / "python"
                    venv_python.parent.mkdir(parents=True, exist_ok=True)
                    log = tmp_path / "recovery.log"
                    venv_python.write_text(
                        "#!/bin/sh\n"
                        # Installer probes recovery python with -c; keep the probe out of the log.
                        + 'if [ "$1" = "-c" ]; then exit 0; fi\n'
                        + 'printf \'%s\\n\' "$@" >> "'
                        + str(log)
                        + '"\n'
                        + "exit 42\n",
                        encoding="utf-8",
                    )
                    venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    if with_launcher:
                        launcher.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
                        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (opos / "manifest").write_text(
                        "launcher="
                        + str(launcher)
                        + "\n"
                        + "venv="
                        + str(opos / "venv")
                        + "\n"
                        + "repo="
                        + recorded
                        + "\n",
                        encoding="utf-8",
                    )
                    keep = opos / "keep"
                    keep.write_bytes(b"keep-bytes")
                    venv_keep = opos / "venv" / "keep"
                    venv_keep.write_bytes(b"venv-keep-bytes")
                    before = {}
                    for p in (opos / "manifest", venv_python, keep, venv_keep):
                        before[str(p)] = p.read_bytes()
                    if with_launcher:
                        before[str(launcher)] = launcher.read_bytes()
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                    )
                    if equivalent:
                        self.assertEqual(proc.returncode, 42, proc.stdout + proc.stderr)
                        self.assertEqual(
                            log.read_text(encoding="utf-8").splitlines(), ["-m", "opos.setup", "uninstall"]
                        )
                    else:
                        self.assertEqual(proc.returncode, 127 if with_launcher else 1, proc.stdout + proc.stderr)
                        self.assertIn(
                            "repo mismatch: run the uninstaller from the original checkout", proc.stderr.splitlines()
                        )
                        self.assertNotIn(recorded, proc.stdout + proc.stderr)
                        self.assertFalse(log.exists())
                    for key, value in before.items():
                        self.assertTrue(pathlib.Path(key).is_file())
                        self.assertEqual(pathlib.Path(key).read_bytes(), value)
                    if not with_launcher:
                        self.assertFalse(launcher.exists())

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_repo_mismatch_diagnostics_hide_recorded_path(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        want = "repo mismatch: run the uninstaller from the original checkout"
        marker = "".join(["private", "-user"])
        cases = [
            ("absent_dry", True, False, 1),
            ("absent_live", False, False, 1),
            ("fails_127", False, True, 127),
        ]
        for name, dry, with_launcher, want_rc in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    bindir = tmp_path / "bin"
                    recorded_dir = tmp_path / marker / "checkout"
                    recorded_dir.mkdir(parents=True)
                    home.mkdir()
                    bindir.mkdir()
                    recorded = str(recorded_dir)
                    venv_python = home / "venv" / "bin" / "python"
                    venv_python.parent.mkdir(parents=True, exist_ok=True)
                    venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    launcher = bindir / "opos"
                    if with_launcher:
                        launcher.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
                        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (home / "manifest").write_text(
                        "launcher="
                        + str(launcher)
                        + "\n"
                        + "venv="
                        + str(home / "venv")
                        + "\n"
                        + "repo="
                        + recorded
                        + "\n",
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    args = ["sh", "--", str(install), "--uninstall"]
                    if dry:
                        args.append("--dry-run")
                    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, want_rc, proc.stdout + proc.stderr)
                    self.assertIn(want, proc.stderr.splitlines())
                    self.assertNotIn(recorded, proc.stdout + proc.stderr)
                    self.assertNotIn(marker, proc.stdout + proc.stderr)

    def test_recovery_python_ignores_caller_cwd(self):
        text = _read_install_sh()
        lines = [
            line
            for line in text.splitlines()
            if "-m opos.setup" in line and ("UVENV/bin/python" in line or "_recovery_python" in line)
        ]
        self.assertEqual(len(lines), 1, "expected one verified recovery entry point")
        for line in lines:
            self.assertTrue(
                "PYTHONSAFEPATH=1" in line or 'cd -- "$ROOT/src"' in line,
                "recovery python -m must not import caller cwd: %r" % line,
            )

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_failed_launcher_recovers_with_system_python(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]
        resolved_root = pathlib.Path(os.path.realpath(str(root)))
        cases = [
            ("exit_1_executable_venv_fails", 1, "file"),
            ("exit_127_dangling_venv", 127, "dangling"),
        ]
        for name, launcher_exit, venv_kind in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    bindir = tmp_path / "bin"
                    tools = tmp_path / "tools"
                    home.mkdir()
                    bindir.mkdir()
                    tools.mkdir()
                    venv_dir = home / "venv"
                    venv_bin = venv_dir / "bin"
                    venv_bin.mkdir(parents=True)
                    venv_python = venv_bin / "python"
                    if venv_kind == "file":
                        venv_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    else:
                        os.symlink(str(tmp_path / "missing-interpreter"), str(venv_python))
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit " + str(launcher_exit) + "\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(resolved_root) + "\n",
                        encoding="utf-8",
                    )
                    log = tmp_path / "recovery.log"
                    log.write_text("", encoding="utf-8")
                    stub = tools / "python3"
                    stub.write_text(
                        "#!/bin/sh\n"
                        + 'if [ "$1" = "-c" ]; then exit 0; fi\n'
                        + 'printf \'%s\\n\' "$*" >> "'
                        + str(log)
                        + '"\n'
                        + 'pwd -P >> "'
                        + str(log)
                        + '"\n'
                        + 'printf \'%s\\n\' "$PYTHONPATH" >> "'
                        + str(log)
                        + '"\n'
                        + 'printf \'%s\\n\' "$PYTHONSAFEPATH" >> "'
                        + str(log)
                        + '"\n'
                        + "exit 42\n",
                        encoding="utf-8",
                    )
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["PATH"] = str(tools) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--keep-config"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 42, proc.stdout + proc.stderr)
                    records = log.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(len(records), 4, records)
                    self.assertEqual(records[0], "-m opos.setup uninstall --keep-config")
                    self.assertEqual(records[1], str(resolved_root / "src"))
                    self.assertEqual(records[2], str(resolved_root / "src"))
                    self.assertEqual(records[3], "1")
                    self.assertTrue(launcher.is_file(), "launcher must be retained on recovery failure")
                    self.assertTrue((home / "manifest").is_file(), "manifest must be retained on recovery failure")
                    self.assertTrue(venv_dir.is_dir(), "venv directory must be retained on recovery failure")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_recovery_ownership_all_branches(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        install = root / "install.sh"
        cases = []
        for mode in ("absent-dry", "absent-exec", "126", "127"):
            for venv in ("owned", "external"):
                cases.append((mode, venv))
        for mode, venv in cases:
            with self.subTest(launcher=mode, venv=venv):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    bindir = tmp_path / "bin"
                    outside = tmp_path / "external"
                    for p in (home, bindir, outside):
                        p.mkdir(parents=True)
                    owned_venv = home / "venv"
                    ext_venv = outside / "venv"
                    picked = owned_venv if venv == "owned" else ext_venv
                    (picked / "bin").mkdir(parents=True)
                    ilog = tmp_path / "interp.log"
                    llog = tmp_path / "launcher.log"
                    interp = picked / "bin" / "python"
                    interp.write_text(
                        "#!/bin/sh\n"
                        + 'if [ "$1" = "-c" ]; then exit 0; fi\n'
                        + 'printf \'%s\\n\' "$*" >> "'
                        + str(ilog)
                        + '"\n'
                        + "exit 42\n",
                        encoding="utf-8",
                    )
                    interp.chmod(interp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    launcher = bindir / "opos"
                    status = None
                    if mode in ("126", "127"):
                        status = int(mode)
                        launcher.write_text(
                            "#!/bin/sh\n"
                            + 'printf \'%s\\n\' "$*" >> "'
                            + str(llog)
                            + '"\n'
                            + "exit "
                            + str(status)
                            + "\n",
                            encoding="utf-8",
                        )
                        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    (home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(picked) + "\nrepo=" + str(root) + "\n",
                        encoding="utf-8",
                    )
                    home_keep = home / "keep"
                    ext_keep = outside / "keep"
                    home_keep.write_bytes(b"".join([b"home-", b"keep-", mode.encode(), b"-", venv.encode()]))
                    ext_keep.write_bytes(b"".join([b"ext-", b"keep-", mode.encode(), b"-", venv.encode()]))
                    snaps = {}
                    for p in (home / "manifest", interp, home_keep, ext_keep):
                        snaps[str(p)] = p.read_bytes()
                    if launcher.is_file():
                        snaps[str(launcher)] = launcher.read_bytes()
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    args = ["sh", str(install), "--uninstall"]
                    dry = mode == "absent-dry"
                    if dry:
                        args.append("--dry-run")
                    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
                    is_owned = venv == "owned"
                    if dry and is_owned:
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        want = (
                            "==> would run recovery entry point "
                            + str(owned_venv / "bin" / "python")
                            + " uninstall --dry-run"
                        )
                        self.assertIn(want, proc.stdout.splitlines())
                        self.assertFalse(ilog.exists())
                    elif dry and not is_owned:
                        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                        self.assertIn("would fail: launcher not found", proc.stderr)
                        self.assertFalse(ilog.exists())
                    elif not dry and is_owned:
                        self.assertEqual(proc.returncode, 42, proc.stdout + proc.stderr)
                        self.assertEqual(ilog.read_text(encoding="utf-8").splitlines(), ["-m opos.setup uninstall"])
                    else:
                        self.assertFalse(ilog.exists())
                        if mode == "absent-exec":
                            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                        else:
                            self.assertEqual(proc.returncode, status, proc.stdout + proc.stderr)
                    if mode in ("126", "127") and not dry:
                        self.assertEqual(llog.read_text(encoding="utf-8").splitlines(), ["uninstall"])
                    else:
                        self.assertFalse(llog.exists())
                    for key, value in snaps.items():
                        self.assertTrue(pathlib.Path(key).is_file(), key)
                        self.assertEqual(pathlib.Path(key).read_bytes(), value, key)

    def test_recovery_verification_is_shared(self):
        text = _read_install_sh()
        self.assertEqual(
            len(re.findall(r"^verified_recovery\(\)", text, re.MULTILINE)), 1, "exactly one helper definition"
        )
        start = text.index("verified_recovery()")
        end = text.index("do_uninstall()", start)
        helper = text[start:end]
        self.assertIn('[ -f "$MANIFEST" ]', helper)
        self.assertRegex(helper, r'\[ "(\$\{UVENV:-\}|\$UVENV)" = "\$OPOS_HOME/venv" \]')
        self.assertIn('opos_owned_path "$UVENV" "$OPOS_HOME/venv"', helper)
        self.assertIn('[ -x "$UVENV/bin/python" ]', helper)
        self.assertIn('[ -d "$ROOT/src/opos" ]', helper)
        self.assertIn('canon_repo "$UREPO"', helper)
        self.assertIn('canon_repo "$ROOT"', helper)
        helper_start = text.index("run_verified_recovery()")
        helper_end = text.index("do_uninstall()", helper_start)
        helper_fn = text[helper_start:helper_end]
        anchor = text.index("do_uninstall()")
        uninstall_body = text[anchor:]
        helper_calls = re.findall(r"if verified_recovery; then", helper_fn)
        body_calls = re.findall(r"if verified_recovery; then", uninstall_body)
        self.assertEqual(len(helper_calls), 1, "exactly one conditional verification call inside execution helper")
        self.assertEqual(len(body_calls), 1, "exactly one conditional verification call inside do_uninstall")
        self.assertNotIn(
            '[ -x "$UVENV/bin/python" ]', uninstall_body, "no executable-Python check may remain in do_uninstall"
        )

    def test_uninstall_recovery_uses_one_helper(self):
        text = _read_install_sh()
        self.assertEqual(
            len(re.findall(r"^run_verified_recovery\(\)", text, re.MULTILINE)),
            1,
            "exactly one execution-helper definition",
        )
        verified_start = text.index("verified_recovery()")
        helper_start = text.index("run_verified_recovery()", verified_start)
        uninstall_start = text.index("do_uninstall()", helper_start)
        verified_block = text[verified_start:helper_start]
        helper_block = text[helper_start:uninstall_start]
        uninstall_body = text[uninstall_start:]
        self.assertRegex(
            helper_block, r"(?m)^\s*if verified_recovery; then", "execution helper must call verification conditionally"
        )
        self.assertIn("_verified=1", helper_block)
        lines = [line for line in text.splitlines() if "UVENV/bin/python" in line and "-m opos.setup" in line]
        self.assertEqual(len(lines), 1, "exactly one verified recovery command")
        self.assertIn(lines[0], helper_block, "recovery command must live inside execution helper")
        calls = re.findall(r'run_verified_recovery "\$@"', uninstall_body)
        self.assertEqual(len(calls), 2, "do_uninstall must delegate twice to execution helper")
        self.assertIn('[ -f "$MANIFEST" ]', verified_block)
        self.assertRegex(verified_block, r'\[ "(\$\{UVENV:-\}|\$UVENV)" = "\$OPOS_HOME/venv" \]')
        self.assertIn('opos_owned_path "$UVENV" "$OPOS_HOME/venv"', verified_block)
        self.assertIn('[ -x "$UVENV/bin/python" ]', verified_block)
        self.assertIn('[ -d "$ROOT/src/opos" ]', verified_block)
        self.assertIn('canon_repo "$UREPO"', verified_block)
        self.assertIn('canon_repo "$ROOT"', verified_block)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_linger_advice_requires_opos_ownership(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def run_uninstall(home, bindir, root, faked):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(faked) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run", "--force"],
                capture_output=True,
                text=True,
                env=env,
            )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            faked = tmp_path / "fakebin"
            for p in (home, bindir, root, faked):
                p.mkdir(parents=True)
            stub = faked / "loginctl"
            stub.write_text("#!/bin/sh\nprintf 'Linger=yes\\n'\nexit 0\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            marker = root / "var" / "lib" / "opos-linger"
            advice = "".join(["disable", "-linger"])
            proc = run_uninstall(home, bindir, root, faked)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(advice, proc.stdout + proc.stderr)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("owned\n", encoding="utf-8")
            proc = run_uninstall(home, bindir, root, faked)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(advice, proc.stdout + proc.stderr)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_linger_advice_real_uninstall_requires_ownership(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def run_real(home, bindir, root, faked):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(faked) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
            )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            faked = tmp_path / "fakebin"
            for p in (home, bindir, root, faked):
                p.mkdir(parents=True)
            trusted = bindir / "opos"
            trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(trusted) + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            login_stub = faked / "loginctl"
            login_stub.write_text("#!/bin/sh\nprintf 'Linger=yes\\n'\nexit 0\n", encoding="utf-8")
            login_stub.chmod(login_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            for name in ("cp", "ufw", "systemctl"):
                stub = faked / name
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            advice = "".join(["disable", "-linger"])
            proc = run_real(home, bindir, root, faked)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(advice, proc.stdout + proc.stderr)
            marker = root / "var" / "lib" / "opos-linger"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("owned\n", encoding="utf-8")
            proc = run_real(home, bindir, root, faked)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(advice, proc.stdout + proc.stderr)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_recovery_stub_positive_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            stub = home / "venv" / "bin" / "python"
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text(
                '#!/bin/sh\nif [ $# -eq 3 ] && [ "$1" = "-m" ] && [ "$2" = "opos.setup" ] && [ "$3" = "uninstall" ]; then\ntouch "$OPOS_RECOVERY_MARKER"\nexit 42\nfi\nexit 1\n',
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            marker = home / ".recovery_invoked"
            env = dict(os.environ)
            env["OPOS_RECOVERY_MARKER"] = str(marker)
            proc = subprocess.run(
                [str(stub), "-m", "opos.setup", "uninstall"], capture_output=True, text=True, env=env, timeout=30
            )
            self.assertEqual(proc.returncode, 42, "stub must exit with distinctive code")
            self.assertTrue(marker.is_file(), "stub must create marker so dry-run check can fail")
            marker.unlink()
            proc = subprocess.run([str(stub)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 1, "stub must fail without recovery entry point")
            self.assertFalse(marker.is_file(), "stub must not create marker without recovery entry point")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_verified_recovery_blocks_each_failed_guard(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def make_venv(home):
            target = home / "venv" / "bin" / "python"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def write_manifest(home, launcher, venv, repo):
            (home / "manifest").write_text(
                "launcher=" + launcher + "\nvenv=" + venv + "\nrepo=" + repo + "\n",
                encoding="utf-8",
            )

        def run_dry(home, bindir, extra=None):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            if extra:
                env.update(extra)
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            make_venv(home)
            write_manifest(home, str(home.parent / "bin" / "opos"), str(home / "venv"), str(root))
            proc = run_dry(home, bindir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            for p in (home, bindir, fakebin):
                p.mkdir(parents=True)
            marker = tmp_path / "recovery-marker"
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 0\n', encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            write_manifest(home, str(bindir / "opos"), str(home / "venv"), str(root))
            for tool in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd"):
                stub = fakebin / tool
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            stat_stub = fakebin / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"], capture_output=True, text=True, env=env, timeout=60
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(marker.is_file(), "valid control must execute recovery")
            self.assertIn("==> restore summary:", proc.stdout)

        failing = ["missing_manifest", "venv_mismatch", "python_missing", "repo_mismatch", "src_missing"]
        for name in failing:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    home = pathlib.Path(tmp) / "home"
                    bindir = pathlib.Path(tmp) / "bin"
                    home.mkdir()
                    bindir.mkdir()
                    extra = None
                    if name == "missing_manifest":
                        make_venv(home)
                    elif name == "venv_mismatch":
                        make_venv(home)
                        write_manifest(
                            home,
                            str(home.parent / "bin" / "opos"),
                            str(pathlib.Path(tmp) / "other" / "venv"),
                            str(root),
                        )
                    elif name == "python_missing":
                        write_manifest(home, str(home.parent / "bin" / "opos"), str(home / "venv"), str(root))
                    elif name == "repo_mismatch":
                        make_venv(home)
                        write_manifest(
                            home,
                            str(home.parent / "bin" / "opos"),
                            str(home / "venv"),
                            str(pathlib.Path(tmp) / "elsewhere"),
                        )
                    elif name == "src_missing":
                        bare = pathlib.Path(tmp) / "bare"
                        bare.mkdir()
                        make_venv(home)
                        write_manifest(home, str(home.parent / "bin" / "opos"), str(home / "venv"), str(bare))
                        extra = {"OPOS_ROOT": str(bare)}
                    if name == "missing_manifest":
                        self.assertFalse((home / "manifest").exists())
                    proc = run_dry(home, bindir, extra)
                    self.assertEqual(proc.returncode, 1, name + " dry-run must fail; " + proc.stdout + proc.stderr)
                    self.assertNotIn("would run recovery entry point", proc.stdout, name)
                    live_marker = pathlib.Path(tmp) / "live-marker"
                    if name != "python_missing":
                        live_python = home / "venv" / "bin" / "python"
                        if live_python.is_file():
                            live_python.write_text(
                                '#!/bin/sh\ntouch "' + str(live_marker) + '"\nexit 0\n', encoding="utf-8"
                            )
                            live_python.chmod(live_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    live_fake = pathlib.Path(tmp) / "live-fake"
                    live_fake.mkdir()
                    for tool in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd"):
                        stub = live_fake / tool
                        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    stat_stub = live_fake / "stat"
                    stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
                    stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    live_env = dict(os.environ)
                    live_env["HOME"] = str(home)
                    live_env["OPOS_HOME"] = str(home)
                    live_env["OPOS_BIN_DIR"] = str(bindir)
                    live_env["PATH"] = str(live_fake) + os.pathsep + live_env.get("PATH", "")
                    if extra:
                        live_env.update(extra)
                    if not live_env.get("USER"):
                        live_env["USER"] = "opos-test-user"
                    live_proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=live_env,
                        timeout=60,
                    )
                    self.assertEqual(
                        live_proc.returncode, 0, name + " must reach fallback; " + live_proc.stdout + live_proc.stderr
                    )
                    self.assertIn("==> restore summary:", live_proc.stdout, name)
                    self.assertFalse(live_marker.is_file(), name + " must not execute recovery")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_refuses_untrusted_sshd_backup(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        dest_literal = "/etc/ssh/" + "sshd_config"
        bak_name = "".join(["sshd_config", ".bak"])
        for kind in ("regular", "symlink", "dangling"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    fakebin = tmp_path / "fakebin"
                    for p in (home, opos_home, bindir, fakebin):
                        p.mkdir(parents=True)
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    # Meta fixture: a valid ownership marker that the fixed installer
                    # will require before restoring; an unrelated refusal alone must not pass.
                    meta = opos_home / "".join(["meta", ".verified"])
                    meta.write_bytes(b"".join([b"own", b"er-", b"ok-1"]))
                    meta_before = meta.read_bytes()
                    launcher_before = launcher.read_bytes()
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    sentinel_file = venv_dir / "".join(["dum", "my"])
                    sentinel_file.write_bytes(b"".join([b"dummy-venv-", b"1"]))
                    sentinel_before = sentinel_file.read_bytes()
                    elsewhere = tmp_path / "elsewhere"
                    (opos_home / "manifest").write_text(
                        "launcher="
                        + str(launcher)
                        + "\nvenv="
                        + str(elsewhere / "venv")
                        + "\nrepo="
                        + str(elsewhere)
                        + "\n",
                        encoding="utf-8",
                    )
                    manifest_before = (opos_home / "manifest").read_bytes()
                    dest = tmp_path / "sshd_config"
                    dest.write_bytes(b"".join([b"dest-bytes-", b"31"]))
                    dest_before = dest.read_bytes()
                    bak = opos_home / bak_name
                    bak_bytes = b"".join([b"bak-bytes-", b"42"])
                    if kind == "regular":
                        bak.write_bytes(bak_bytes)
                    elif kind == "symlink":
                        real = tmp_path / "real"
                        real.write_bytes(bak_bytes)
                        os.symlink(str(real), str(bak))
                    else:
                        target = tmp_path / "missing-target"
                        os.symlink(str(target), str(bak))
                        self.assertFalse(bak.exists())
                        self.assertTrue(bak.is_symlink())
                    script = tmp_path / "install-copy.sh"
                    script.write_bytes(install.read_bytes().replace(dest_literal.encode(), str(dest).encode()))
                    call_log = tmp_path / "".join(["call", ".log"])
                    call_log.write_text("", encoding="utf-8")
                    for tool in ("ufw", "systemctl"):
                        stub = fakebin / tool
                        stub.write_text(
                            "#!/bin/sh\nprintf '%s\\n' \"" + tool + ' $*" >> "' + str(call_log) + '"\nexit 0\n',
                            encoding="utf-8",
                        )
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(tmp_path / "root")
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(script), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn(str(bak), proc.stderr)
                    self.assertIn("".join(["man", "ual"]), proc.stderr.lower())
                    self.assertEqual(dest.read_bytes(), dest_before)
                    self.assertEqual(launcher.read_bytes(), launcher_before)
                    self.assertEqual(sentinel_file.read_bytes(), sentinel_before)
                    self.assertEqual((opos_home / "manifest").read_bytes(), manifest_before)
                    self.assertEqual(meta.read_bytes(), meta_before, "ownership fixture must be preserved")
                    self.assertTrue(bak.is_symlink() or bak.exists(), "backup must be preserved")
                    if kind == "regular":
                        self.assertEqual(bak.read_bytes(), bak_bytes)
                    calls = call_log.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(calls, [], "no firewall/service calls allowed")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_dir_validation_and_trailing_slash(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def run_with(home_dir, home_env, bin_env):
            venv_python = home_dir / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home_dir / "manifest").write_text(
                "launcher="
                + str(home_dir.parent / "bin" / "opos")
                + "\n"
                + "venv="
                + str(home_dir / "venv")
                + "\n"
                + "repo="
                + str(root)
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPOS_HOME"] = home_env
            env["OPOS_BIN_DIR"] = bin_env
            env["HOME"] = str(home_dir)
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
            )

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            proc = run_with(home, str(home) + "/", str(bindir) + "/")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            proc = run_with(home, "", str(bindir))
            self.assertNotIn("would run recovery entry point", proc.stdout)
            self.assertEqual(proc.returncode, 1)

        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp) / "home"
            bindir = pathlib.Path(tmp) / "bin"
            home.mkdir()
            bindir.mkdir()
            proc = run_with(home, "/", "/")
            self.assertNotIn("would run recovery entry point", proc.stdout)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_real_uninstall_preserves_unrelated_sentinels(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_real(home, bindir, root, fakebin, extra_env=None):
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            if extra_env:
                env.update(extra_env)
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        with self.subTest(name="manifest_paths_outside_ownership"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                outside = tmp_path / "outside"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, opos_home, bindir, outside, fakebin, root):
                    p.mkdir(parents=True)
                trusted = bindir / "opos"
                trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                sentinel_launcher = outside / "".join(["unre", "lated-launcher"])
                sentinel_launcher.write_bytes(b"".join([b"senti", b"nel-launcher-", b"7"]))
                sentinel_venv = outside / "".join(["unre", "lated-venv"])
                sentinel_venv.mkdir()
                sentinel_file = sentinel_venv / "".join(["dum", "my"])
                sentinel_file.write_bytes(b"".join([b"senti", b"nel-venv-", b"9"]))
                (opos_home / "manifest").write_text(
                    "launcher="
                    + str(sentinel_launcher)
                    + "\n"
                    + "venv="
                    + str(sentinel_venv)
                    + "\n"
                    + "repo="
                    + str(home)
                    + "\n",
                    encoding="utf-8",
                )
                for name in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd", "stat"):
                    if name == "systemctl":
                        write_stub(fakebin / name, 'case "$*" in *list-unit-files*) exit 0;; esac\nexit 0\n')
                    elif name == "stat":
                        write_stub(fakebin / name, "printf '%s\\n' \"0 600\"\n")
                    elif name == "sshd":
                        write_stub(fakebin / name, "exit 0\n")
                    else:
                        write_stub(fakebin / name, "exit 0\n")
                proc = run_real(opos_home, bindir, root, fakebin)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertTrue(sentinel_launcher.is_file(), "untrusted manifest launcher must survive real uninstall")
                self.assertEqual(sentinel_launcher.read_bytes(), b"".join([b"senti", b"nel-launcher-", b"7"]))
                self.assertTrue(sentinel_file.is_file(), "untrusted manifest venv content must survive real uninstall")
                self.assertEqual(sentinel_file.read_bytes(), b"".join([b"senti", b"nel-venv-", b"9"]))

        with self.subTest(name="invalid_directories"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                bindir = tmp_path / "bin"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, bindir, fakebin, root):
                    p.mkdir(parents=True)
                sentinel = tmp_path / "".join(["unre", "lated-sentinel"])
                sentinel.write_bytes(b"".join([b"senti", b"nel-invalid-", b"3"]))
                for name in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd", "stat"):
                    if name == "systemctl":
                        write_stub(fakebin / name, 'case "$*" in *list-unit-files*) exit 1;; esac\nexit 0\n')
                    elif name == "stat":
                        write_stub(fakebin / name, "printf '%s\\n' \"0 600\"\n")
                    elif name == "sshd":
                        write_stub(fakebin / name, "exit 0\n")
                    else:
                        write_stub(fakebin / name, "exit 0\n")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = "/"
                env["OPOS_BIN_DIR"] = "/"
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                proc = subprocess.run(
                    ["sh", str(install), "--uninstall", "--force"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=60,
                )
                self.assertNotEqual(proc.returncode, 0)
                self.assertTrue(sentinel.is_file(), "unrelated sentinel must survive invalid directory uninstall")
                self.assertEqual(sentinel.read_bytes(), b"".join([b"senti", b"nel-invalid-", b"3"]))

        with self.subTest(name="owned_artifacts_removed"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp)
                home = tmp_path / "home"
                opos_home = tmp_path / "opos"
                bindir = tmp_path / "bin"
                outside = tmp_path / "outside"
                fakebin = tmp_path / "fakebin"
                root = tmp_path / "root"
                for p in (home, opos_home, bindir, outside, fakebin, root):
                    p.mkdir(parents=True)
                owned_launcher = bindir / "opos"
                owned_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                owned_launcher.chmod(owned_launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                owned_venv = opos_home / "venv"
                owned_venv.mkdir(parents=True)
                owned_file = owned_venv / "".join(["dum", "my"])
                owned_file.write_bytes(b"".join([b"owned-venv-", b"4"]))
                (opos_home / "manifest").write_text(
                    "launcher="
                    + str(owned_launcher)
                    + "\n"
                    + "venv="
                    + str(owned_venv)
                    + "\n"
                    + "repo="
                    + str(home)
                    + "\n",
                    encoding="utf-8",
                )
                unrelated = outside / "".join(["unre", "lated-keep"])
                unrelated.write_bytes(b"".join([b"senti", b"nel-keep-", b"5"]))
                bin_sentinel = bindir / "".join(["unre", "lated-bin"])
                bin_sentinel.write_bytes(b"".join([b"senti", b"nel-bin-", b"6"]))
                home_sentinel = opos_home / "".join(["unre", "lated-home"])
                home_sentinel.write_bytes(b"".join([b"senti", b"nel-home-", b"8"]))
                for name in ("ufw", "systemctl", "loginctl", "nft", "cp", "sshd", "stat"):
                    if name == "systemctl":
                        write_stub(fakebin / name, 'case "$*" in *list-unit-files*) exit 0;; esac\nexit 0\n')
                    elif name == "stat":
                        write_stub(fakebin / name, "printf '%s\\n' \"0 600\"\n")
                    elif name == "sshd":
                        write_stub(fakebin / name, "exit 0\n")
                    else:
                        write_stub(fakebin / name, "exit 0\n")
                proc = run_real(opos_home, bindir, root, fakebin)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertFalse(owned_launcher.exists(), "owned launcher must be removed on successful uninstall")
                self.assertFalse(owned_venv.exists(), "validated venv must be removed on successful uninstall")
                self.assertTrue(opos_home.is_dir(), "home with unrelated files must be preserved")
                self.assertFalse((opos_home / "manifest").exists(), "manifest must be removed on successful uninstall")
                self.assertTrue(unrelated.is_file(), "unrelated artifact must survive successful uninstall")
                self.assertEqual(unrelated.read_bytes(), b"".join([b"senti", b"nel-keep-", b"5"]))
                self.assertTrue(bin_sentinel.is_file(), "unrelated bindir sentinel must survive successful uninstall")
                self.assertEqual(bin_sentinel.read_bytes(), b"".join([b"senti", b"nel-bin-", b"6"]))
                self.assertTrue(
                    home_sentinel.is_file(), "unrelated opos_home sentinel must survive successful uninstall"
                )
                self.assertEqual(home_sentinel.read_bytes(), b"".join([b"senti", b"nel-home-", b"8"]))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_dir_validation_isolated(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def run_isolated(home_dir, home_env, bin_env):
            venv_python = home_dir / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home_dir / "manifest").write_text(
                "launcher="
                + str(home_dir.parent / "bin" / "opos")
                + "\n"
                + "venv="
                + str(home_dir / "venv")
                + "\n"
                + "repo="
                + str(root)
                + "\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["OPOS_HOME"] = home_env
            env["OPOS_BIN_DIR"] = bin_env
            env["HOME"] = str(home_dir)
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            return subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
            )

        cases = [
            ("home_invalid_bin_valid", "/", None),
            ("home_valid_bin_invalid", None, "/"),
        ]
        for name, home_override, bin_override in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    home = pathlib.Path(tmp) / "home"
                    bindir = pathlib.Path(tmp) / "bin"
                    home.mkdir()
                    bindir.mkdir()
                    good = run_isolated(home, str(home), str(bindir))
                    good_msg = name + " stdout=" + good.stdout + " stderr=" + good.stderr
                    self.assertEqual(good.returncode, 0, good_msg)
                    self.assertIn("would run recovery entry point", good.stdout, good_msg)
                    home_env = home_override if home_override is not None else str(home)
                    bin_env = bin_override if bin_override is not None else str(bindir)
                    proc = run_isolated(home, home_env, bin_env)
                    msg = name + " stdout=" + proc.stdout + " stderr=" + proc.stderr
                    self.assertEqual(proc.returncode, 1, msg)
                    self.assertIn("==> would fail: launcher not found ", proc.stderr.splitlines(), msg)
                    self.assertNotIn("would run recovery entry point", proc.stdout, msg)
                    self.assertNotIn("would run recovery entry point", proc.stderr, msg)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_custom_bin_launcher_is_named_when_fallback_refuses_it(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            custombin = tmp_path / "custombin"
            fakebin = tmp_path / "fakebin"
            for p in (home, bindir, custombin, fakebin):
                p.mkdir(parents=True)
            custom_launcher = custombin / "opos"
            custom_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            custom_launcher.chmod(custom_launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher="
                + str(custom_launcher)
                + "\n"
                + "venv="
                + str(home / "venv")
                + "\n"
                + "repo="
                + str(root)
                + "\n",
                encoding="utf-8",
            )
            for name in ("ufw", "loginctl", "nft"):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(custom_launcher.is_file(), "fallback must leave the untrusted launcher behind")
            self.assertIn("not removing", proc.stderr)
            self.assertIn(str(custom_launcher), proc.stderr)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_verified_recovery_enforces_each_prerequisite(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]
        text = _read_install_sh()
        hstart = text.index("verified_recovery()")
        hend = text.index("do_uninstall()", hstart)
        helper = text[hstart:hend]
        prereqs = [
            ("manifest", r'\[ -f "\$MANIFEST" \]'),
            ("venv_equality", r'\[ "(\$\{UVENV:-\}|\$UVENV)" = "\$OPOS_HOME/venv" \]'),
            ("venv_ownership", r'opos_owned_path "(\$\{UVENV:-\}|\$UVENV)" "\$OPOS_HOME/venv"'),
            ("recovery_python", r'\[ -x "\$UVENV/bin/python" \]'),
            ("src_dir", r'\[ -d "\$ROOT/src/opos" \]'),
            ("repo_match", r'canon_repo "\$UREPO"'),
        ]

        def make_valid(tmp_path):
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            home.mkdir()
            bindir.mkdir()
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            marker = tmp_path / "recovery-marker"
            venv_python.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 0\n', encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher="
                + str(tmp_path / "bin" / "opos")
                + "\n"
                + "venv="
                + str(home / "venv")
                + "\n"
                + "repo="
                + str(root)
                + "\n",
                encoding="utf-8",
            )
            return home, bindir, marker

        def run_copy(script_path, home, bindir):
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["HOME"] = str(home)
            return subprocess.run(
                ["sh", str(script_path), "--uninstall", "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home, bindir, marker = make_valid(tmp_path)
            proc = run_copy(install, home, bindir)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("would run recovery entry point", proc.stdout)
        for label, pat in prereqs:
            with self.subTest(prereq=label):
                found = re.search(pat, helper)
                self.assertIsNotNone(found, label + " prerequisite must exist")
                mutated_helper = helper[: found.start()] + "false" + helper[found.end() :]
                mutated = text[:hstart] + mutated_helper + text[hend:]
                self.assertNotEqual(mutated, text)
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home, bindir, marker = make_valid(tmp_path)
                    copy_path = tmp_path / "install-copy.sh"
                    copy_path.write_text(mutated, encoding="utf-8")
                    proc = run_copy(copy_path, home, bindir)
                    self.assertNotEqual(proc.returncode, 0, label)
                    self.assertNotIn("would run recovery entry point", proc.stdout, label)
                    self.assertFalse(marker.is_file(), label + " recovery python must not execute")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_verified_recovery_actual_uninstall_marker_control(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        root = pathlib.Path(__file__).resolve().parents[1]

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def make_valid(tmp_path):
            home = tmp_path / "home"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            for p in (home, bindir, fakebin):
                p.mkdir(parents=True)
            marker = tmp_path / "recovery-marker"
            venv_python = home / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 0\n', encoding="utf-8")
            venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (home / "manifest").write_text(
                "launcher=" + str(bindir / "opos") + "\nvenv=" + str(home / "venv") + "\nrepo=" + str(root) + "\n",
                encoding="utf-8",
            )
            write_stub(fakebin / "ufw", "exit 0\n")
            write_stub(fakebin / "systemctl", 'case "$*" in *list-unit-files*) exit 0;; esac\nexit 0\n')
            for name in ("loginctl", "nft", "cp", "sshd"):
                write_stub(fakebin / name, "exit 0\n")
            write_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"\n")
            return home, bindir, fakebin, marker

        def run_real(home, bindir, fakebin, extra=None, script_path=install):
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            if extra:
                env.update(extra)
            return subprocess.run(
                ["sh", str(script_path), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home, bindir, fakebin, marker = make_valid(tmp_path)
            proc = run_real(home, bindir, fakebin)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(marker.is_file(), "valid actual uninstall must execute recovery")

        cases = ("missing_manifest", "venv_mismatch", "python_missing", "repo_mismatch", "src_missing")
        for name in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home, bindir, fakebin, marker = make_valid(tmp_path)
                    extra = None
                    script_path = install
                    if name == "missing_manifest":
                        (home / "manifest").unlink()
                    elif name == "venv_mismatch":
                        (home / "manifest").write_text(
                            "launcher="
                            + str(bindir / "opos")
                            + "\nvenv="
                            + str(tmp_path / "other" / "venv")
                            + "\nrepo="
                            + str(root)
                            + "\n",
                            encoding="utf-8",
                        )
                    elif name == "python_missing":
                        target = home / "venv" / "bin" / "python"
                        if target.is_file() or target.is_symlink():
                            target.unlink()
                    elif name == "repo_mismatch":
                        (home / "manifest").write_text(
                            "launcher="
                            + str(bindir / "opos")
                            + "\nvenv="
                            + str(home / "venv")
                            + "\nrepo="
                            + str(tmp_path / "elsewhere")
                            + "\n",
                            encoding="utf-8",
                        )
                    elif name == "src_missing":
                        bare = tmp_path / "bare"
                        bare.mkdir()
                        (home / "manifest").write_text(
                            "launcher="
                            + str(bindir / "opos")
                            + "\nvenv="
                            + str(home / "venv")
                            + "\nrepo="
                            + str(bare)
                            + "\n",
                            encoding="utf-8",
                        )
                        install_src = bare / "install.sh"
                        install_src.write_bytes(install.read_bytes())
                        script_path = install_src
                    proc = run_real(home, bindir, fakebin, extra, script_path=script_path)
                    msg = name + " must prevent recovery execution; stdout=" + proc.stdout + "; stderr=" + proc.stderr
                    self.assertEqual(proc.returncode, 0, msg)
                    self.assertIn("==> restore summary:", proc.stdout, msg)
                    self.assertFalse(marker.is_file(), msg)
                    if name != "missing_manifest":
                        self.assertIn(
                            "warning: force uninstall without verified recovery entry point, removing files only",
                            proc.stderr,
                            msg,
                        )
                    if name == "repo_mismatch":
                        self.assertIn("repo mismatch: run the uninstaller from the original checkout", proc.stderr, msg)
                    if name == "src_missing":
                        self.assertNotIn(
                            "repo mismatch: run the uninstaller from the original checkout",
                            proc.stdout + proc.stderr,
                            msg,
                        )

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_untrusted_launcher_warning_omits_control_characters(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        first = "".join(["reject", "ed-"])
        second = "".join(["FOR", "GED"])
        esc = chr(27)
        cret = chr(13)
        bad_prefix = first
        bad_suffix = second
        for name, extra in (("normal", []), ("dry_run", ["--dry-run"])):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    bindir = tmp_path / "bin"
                    stubdir = tmp_path / "stubs"
                    home.mkdir()
                    bindir.mkdir()
                    stubdir.mkdir()
                    trusted = bindir / "opos"
                    trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    trusted.chmod(trusted.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    launcher_value = "".join([first, esc, "[2J", cret, second])
                    (home / "manifest").write_text(
                        "launcher="
                        + launcher_value
                        + "\n"
                        + "venv="
                        + str(home / "venv")
                        + "\n"
                        + "repo="
                        + str(home)
                        + "\n",
                        encoding="utf-8",
                    )
                    for tool in ("ufw", "loginctl", "nft"):
                        stub = stubdir / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_stub = stubdir / "systemctl"
                    ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"] + extra,
                        capture_output=True,
                        env=env,
                        timeout=30,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    first_line = b"warning: ignoring untrusted launcher in manifest: [redacted]\n"
                    second_line = b"warning: not removing launcher outside trusted location: [redacted]\n"
                    self.assertIn(first_line, proc.stderr)
                    self.assertIn(second_line, proc.stderr)
                    self.assertIn(first_line.strip(), proc.stderr.splitlines())
                    self.assertIn(second_line.strip(), proc.stderr.splitlines())
                    self.assertNotIn(bad_prefix.encode(), proc.stdout)
                    self.assertNotIn(bad_prefix.encode(), proc.stderr)
                    self.assertNotIn(bad_suffix.encode(), proc.stdout)
                    self.assertNotIn(bad_suffix.encode(), proc.stderr)
                    self.assertNotIn(esc.encode(), proc.stdout)
                    self.assertNotIn(esc.encode(), proc.stderr)
                    self.assertNotIn(cret.encode(), proc.stdout)
                    self.assertNotIn(cret.encode(), proc.stderr)
                    if extra:
                        dry_line = b"==> warning: ignoring untrusted launcher in manifest: [redacted]\n"
                        run_line = ("==> would run " + str(trusted) + " uninstall --dry-run").encode() + b"\n"
                        self.assertIn(dry_line, proc.stdout)
                        self.assertIn(run_line, proc.stdout)
                        self.assertIn(dry_line.strip(), proc.stdout.splitlines())
                        self.assertIn(run_line.strip(), proc.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
