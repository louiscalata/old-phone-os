# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

# Direct scripts under -P and -I need the checkout root before importing shared helpers.
if not __package__:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.install_review_support import (
    artifact_data,
    read_install_sh,
    uninstall_restore_block,
)


class TestInstallReview(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_fallback_dry_run_lists_exact_actions(self):
        text = read_install_sh()
        anchor = text.index("do_uninstall()")
        start = text.index("FOUND=0", anchor)
        end = text.index('opos_owned_path "$LAUNCHER"', start)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        self.assertIn(dest_literal, fragment)
        cases = [
            ("all_present", True, True, True),
            ("destination_missing", True, False, True),
            ("all_absent", False, False, False),
            ("files_present_tools_absent", True, True, False),
        ]
        for name, files_present, dest_present, tools_present in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    home.mkdir()
                    opos_home.mkdir()
                    unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                    bak = opos_home / "sshd_config.bak"
                    state = opos_home / "firewall.state"
                    dest = tmp_path / "sshd_config"
                    if files_present:
                        bak.write_bytes(b"backup-bytes-1")
                        state.write_bytes(b"state-bytes-2")
                        unit.parent.mkdir(parents=True, exist_ok=True)
                        unit.write_bytes(b"unit-bytes-3")
                    if dest_present:
                        dest.write_bytes(b"dest-bytes-4")
                    before = {}
                    for p in (bak, state, unit, dest):
                        if p.is_file() and not p.is_symlink():
                            before[str(p)] = p.read_bytes()
                    stubdir = tmp_path / "stubs"
                    stubdir.mkdir()
                    marker = tmp_path / "invoked"
                    if tools_present:
                        for tool in ("ufw", "systemctl"):
                            stub = stubdir / tool
                            stub.write_text('#!/bin/sh\n: > "' + str(marker) + '"\nexit 0\n', encoding="utf-8")
                            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    replaced = fragment.replace(dest_literal, str(dest))
                    preamble = (
                        "is_hardening_active() { return 1; }\n"
                        + "print_hardening_instructions() { :; }\n"
                        + "opos_owns_linger() { return 1; }\n"
                        + "is_linger_enabled() { return 1; }\n"
                    )
                    runner = tmp_path / "run.sh"
                    runner.write_text(preamble + replaced, encoding="utf-8")
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["DRY"] = "1"
                    env["PATH"] = str(stubdir)
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(["/bin/sh", str(runner)], capture_output=True, text=True, env=env)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    lines = proc.stdout.splitlines()
                    if files_present and dest_present and tools_present:
                        expected = [
                            "==> would remove " + str(bak),
                            "==> would overwrite " + str(dest) + " from " + str(bak),
                            "==> would run ufw --force disable",
                            "==> would remove " + str(state),
                            "==> would run systemctl --user disable --now opos-server.service",
                            "==> would run systemctl --user daemon-reload",
                            "==> would remove " + str(unit),
                        ]
                        self.assertEqual(lines, expected)
                    elif files_present and not dest_present and tools_present:
                        expected = [
                            "==> would remove " + str(bak),
                            "==> would run ufw --force disable",
                            "==> would remove " + str(state),
                            "==> would run systemctl --user disable --now opos-server.service",
                            "==> would run systemctl --user daemon-reload",
                            "==> would remove " + str(unit),
                        ]
                        self.assertEqual(lines, expected)
                        self.assertNotIn("would overwrite", proc.stdout)
                    elif not files_present and not dest_present and not tools_present:
                        self.assertEqual(lines, [])
                        self.assertNotIn("would remove", proc.stdout)
                        self.assertNotIn("would overwrite", proc.stdout)
                        self.assertNotIn("would run", proc.stdout)
                    else:
                        expected = [
                            "==> would remove " + str(bak),
                            "==> would overwrite " + str(dest) + " from " + str(bak),
                            "==> would remove " + str(state),
                            "==> would remove " + str(unit),
                        ]
                        self.assertEqual(lines, expected)
                        self.assertNotIn("ufw --force disable", proc.stdout)
                        self.assertNotIn("systemctl --user disable --now", proc.stdout)
                        self.assertNotIn("systemctl --user daemon-reload", proc.stdout)
                    self.assertFalse(marker.exists(), "dry-run must not execute stubs")
                    for key, value in before.items():
                        self.assertEqual(pathlib.Path(key).read_bytes(), value)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_dry_run_stub_execution_is_detectable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            marker = tmp_path / "invoked"
            for tool in ("ufw", "systemctl"):
                stub = stubdir / tool
                stub.write_text('#!/bin/sh\n: > "' + str(marker) + '"\nexit 0\n', encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["PATH"] = str(stubdir)
            for tool in ("ufw", "systemctl"):
                if marker.exists():
                    marker.unlink()
                proc = subprocess.run([str(stubdir / tool)], capture_output=True, text=True, env=env, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(marker.is_file(), tool + " stub must create marker under restricted PATH")
                with self.assertRaises(AssertionError):
                    self.assertFalse(marker.exists(), "dry-run must not execute stubs")
        text = read_install_sh()
        prod_dest = "/etc/ssh/" + "sshd_config"
        prod_dropin = "/etc/ssh/" + "sshd_config.d/60-opos.conf"
        self.assertIn(prod_dest, text)
        self.assertIn(prod_dropin, text)
        marker_text = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker_text)
        tail = text[start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[start : start + used]
        self.assertIn(prod_dest, block)
        self.assertNotRegex(block, r'>\s*"/etc/ssh/')
        self.assertNotRegex(block, r'>>\s*"/etc/ssh/')
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            bak_leaf = "".join(["sshd_config", ".bak"])
            bak = opos_home / bak_leaf
            bak_bytes = b"".join([b"backup-bytes-", b"71"])
            bak.write_bytes(bak_bytes)
            dropin = tmp_path / "no-dropin"
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            prod_prefix = "/etc/" + "ssh/"

            def write_stub(path, body):
                path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            write_stub(stubdir / "stat", "printf '%s\\n' \"0 600\"\n")
            write_stub(stubdir / "sshd", 'printf \'sshd %s\\n\' "$*" >> "' + str(ops) + '"\nexit 0\n')
            write_stub(
                stubdir / "mktemp",
                'printf \'mktemp %s\\n\' "$*" >> "'
                + str(ops)
                + '"\nstaged="$OPOS_TMPDIR/staged.tmp"\n: > "$staged"\nprintf \'%s\\n\' "$staged"\nexit 0\n',
            )
            write_stub(
                stubdir / "cp",
                'printf \'cp %s\\n\' "$*" >> "'
                + str(ops)
                + '"\ncase "$*" in *"$OPOS_PROD_PREFIX"*) exit 1;; esac\nexec /bin/cp "$@"\n',
            )
            write_stub(
                stubdir / "mv",
                'printf \'mv %s\\n\' "$*" >> "'
                + str(ops)
                + '"\ncase "$*" in *"$OPOS_PROD_PREFIX"*) exit 1;; esac\nexec /bin/mv "$@"\n',
            )
            write_stub(
                stubdir / "rm",
                'printf \'rm %s\\n\' "$*" >> "'
                + str(ops)
                + '"\ncase "$*" in *"$OPOS_PROD_PREFIX"*) exit 1;; esac\nexec /bin/rm "$@"\n',
            )
            script = '_sshd="skipped"\n_sshd_dropin="' + str(dropin) + '"\n' + block + "\nprintf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_TMPDIR"] = str(tmp_path)
            env["OPOS_PROD_PREFIX"] = prod_prefix
            env["PATH"] = str(stubdir) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "failed")
            self.assertEqual(bak.read_bytes(), bak_bytes)
            ops_text = ops.read_text(encoding="utf-8")
            self.assertIn(prod_dest, ops_text)
            self.assertIn("mv ", ops_text)
            self.assertFalse((tmp_path / "staged.tmp").exists())
            self.assertFalse(dropin.exists())

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_dry_run_probe_outcomes_never_mutate(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        table_line = "".join(["table inet opos_", "filter"])
        linger_line = "".join(["Linger=", "yes"])
        mutating_nft = "".join(["delete ", "table"])
        mutating_linger = "".join(["disable", "-linger"])
        cases = (
            ("nft_ok_login_ok", True, True),
            ("nft_ok_login_fail", True, False),
            ("nft_fail_login_ok", False, True),
            ("nft_fail_login_fail", False, False),
        )
        for name, nft_ok, login_ok in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    fakebin = tmp_path / "fakebin"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, fakebin, root):
                        p.mkdir(parents=True)
                    (root / "var" / "lib" / "opos-linger").parent.mkdir(parents=True, exist_ok=True)
                    (root / "var" / "lib" / "opos-linger").write_text("owned\n", encoding="utf-8")
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                    os.symlink("".join(["dum", "my"]), str(venv_dir / "".join(["lin", "k-ok"])))
                    os.symlink("".join(["miss", "ing-target"]), str(venv_dir / "".join(["lin", "k-bad"])))
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                        encoding="utf-8",
                    )
                    state = opos_home / "".join(["fire", "wall.state"])
                    state_bytes = b"".join([b"state-bytes-", b"9"])
                    state.write_bytes(state_bytes)
                    unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                    unit.parent.mkdir(parents=True, exist_ok=True)
                    unit_bytes = b"".join([b"unit-bytes-", b"5"])
                    unit.write_bytes(unit_bytes)
                    markers = {}
                    for tool in ("ufw", "systemctl"):
                        marker = tmp_path / (tool + ".invoked")
                        markers[tool] = marker
                        stub = fakebin / tool
                        stub.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 0\n', encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    nft_log = tmp_path / "nft.argv"
                    nft_stub = fakebin / "nft"
                    if nft_ok:
                        nft_body = (
                            'case "$*" in "list tables") printf \'%s\\n\' "' + table_line + '"\nexit 0;; esac\nexit 1\n'
                        )
                    else:
                        nft_body = 'case "$*" in "list tables") exit 1;; esac\nexit 1\n'
                    nft_stub.write_text(
                        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(nft_log) + '"\n' + nft_body, encoding="utf-8"
                    )
                    nft_stub.chmod(nft_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    loginctl_log = tmp_path / "loginctl.argv"
                    loginctl_stub = fakebin / "loginctl"
                    if login_ok:
                        login_body = (
                            'case "$*" in "show-user "*) printf \'%s\\n\' "'
                            + linger_line
                            + '"\nexit 0;; esac\nexit 1\n'
                        )
                    else:
                        login_body = 'case "$*" in "show-user "*) exit 1;; esac\nexit 1\n'
                    loginctl_stub.write_text(
                        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(loginctl_log) + '"\n' + login_body,
                        encoding="utf-8",
                    )
                    loginctl_stub.chmod(loginctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    manifest = opos_home / "manifest"
                    linger = root / "var" / "lib" / "".join(["opos-", "linger"])
                    user = env["USER"]
                    recorded = [launcher, manifest, state, unit, linger, venv_dir]
                    for _base, _dirs, _files in os.walk(str(venv_dir)):
                        for _n in _dirs + _files:
                            _full = pathlib.Path(_base) / _n
                            if str(_full) not in [str(_p) for _p in recorded]:
                                recorded.append(_full)
                    snaps = {}
                    for _p in recorded:
                        _st = os.lstat(str(_p))
                        _mode = stat.S_IMODE(_st.st_mode)
                        if stat.S_ISLNK(_st.st_mode):
                            _kind = "link"
                        elif stat.S_ISDIR(_st.st_mode):
                            _kind = "dir"
                        elif stat.S_ISREG(_st.st_mode):
                            _kind = "file"
                        else:
                            _kind = "other"
                        _data = artifact_data(str(_p), _kind)
                        snaps[str(_p)] = (_kind, _mode, _data)
                    venv_inventory = sorted(
                        os.path.relpath(str(_p), str(venv_dir))
                        for _p in recorded
                        if str(_p) == str(venv_dir) or str(_p).startswith(str(venv_dir) + os.sep)
                    )
                    venv_inventory = sorted(
                        os.path.relpath(os.path.join(_b, _n), str(venv_dir))
                        for _b, _ds, _fs in os.walk(str(venv_dir))
                        for _n in _ds + _fs
                    )
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--dry-run", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn("".join(["--", "force disable"]), proc.stdout)
                    self.assertIn("disable --now opos-server.service", proc.stdout)
                    nft_advice = "".join(["==> then run: sudo nft ", "delete table inet opos_", "filter"])
                    if nft_ok:
                        self.assertIn(nft_advice, proc.stdout)
                    else:
                        self.assertNotIn(nft_advice, proc.stdout)
                    self.assertIn(
                        "".join(["==> linger is enabled: run sudo loginctl ", "disable", "-linger ", user]), proc.stdout
                    )
                    for tool, marker in markers.items():
                        self.assertFalse(marker.exists(), tool + " must not run during dry-run")
                    _current = sorted(
                        os.path.relpath(os.path.join(_b, _n), str(venv_dir))
                        for _b, _ds, _fs in os.walk(str(venv_dir))
                        for _n in _ds + _fs
                    )
                    self.assertEqual(
                        sorted(_current), venv_inventory, "virtual-environment inventory must be unchanged"
                    )
                    for _p_str, (_kind, _mode, _data) in snaps.items():
                        self.assertTrue(os.path.lexists(_p_str), "artifact must still exist: " + _p_str)
                        _st = os.lstat(_p_str)
                        if stat.S_ISLNK(_st.st_mode):
                            _now_kind = "link"
                        elif stat.S_ISDIR(_st.st_mode):
                            _now_kind = "dir"
                        elif stat.S_ISREG(_st.st_mode):
                            _now_kind = "file"
                        else:
                            _now_kind = "other"
                        self.assertEqual(_now_kind, _kind, "artifact type must be unchanged: " + _p_str)
                        self.assertEqual(
                            stat.S_IMODE(_st.st_mode), _mode, "artifact permissions must be unchanged: " + _p_str
                        )
                        self.assertEqual(
                            artifact_data(_p_str, _now_kind), _data, "artifact bytes must be unchanged: " + _p_str
                        )
                    self.assertTrue(nft_log.is_file(), "nft probe must run")
                    self.assertTrue(loginctl_log.is_file(), "loginctl probe must run")
                    nft_calls = nft_log.read_text(encoding="utf-8").splitlines()
                    loginctl_calls = loginctl_log.read_text(encoding="utf-8").splitlines()
                    self.assertTrue(nft_calls, "nft log must be nonempty")
                    self.assertTrue(loginctl_calls, "loginctl log must be nonempty")
                    for line in nft_calls:
                        self.assertEqual(line, "list tables")
                    for line in loginctl_calls:
                        self.assertEqual(line, "show-user " + user)
                    combined = "\n".join(nft_calls + loginctl_calls)
                    self.assertNotIn(mutating_nft, combined)
                    self.assertNotIn(mutating_linger, combined)

    def test_artifact_data_detects_symlink_retargeting(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            first = tmp_path / "".join(["fi", "rst"])
            second = tmp_path / "".join(["sec", "ond"])
            payload = b"".join([b"same-", b"bytes-1"])
            first.write_bytes(payload)
            second.write_bytes(payload)
            link = tmp_path / "".join(["li", "nk"])
            os.symlink(first.name, str(link))
            saved = artifact_data(str(link), "link")
            self.assertEqual(saved, first.name)
            os.unlink(str(link))
            os.symlink(second.name, str(link))
            current = artifact_data(str(link), "link")
            self.assertEqual(current, second.name)
            self.assertNotEqual(saved, current)
            missing_one = "".join(["miss", "ing-one"])
            missing_two = "".join(["miss", "ing-two"])
            os.unlink(str(link))
            os.symlink(missing_one, str(link))
            saved = artifact_data(str(link), "link")
            self.assertEqual(saved, missing_one)
            os.unlink(str(link))
            os.symlink(missing_two, str(link))
            current = artifact_data(str(link), "link")
            self.assertEqual(current, missing_two)
            self.assertNotEqual(saved, current)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_dry_run_preserves_firewall_state_and_service_unit(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_leaf = "".join(["fire", "wall.state"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, fakebin, root):
                p.mkdir(parents=True)
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_dir = opos_home / "venv"
            venv_dir.mkdir(parents=True)
            (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
            (opos_home / "manifest").write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            state = opos_home / state_leaf
            state_bytes = b"".join([b"state-bytes-", b"77"])
            state.write_bytes(state_bytes)
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit_bytes = b"".join([b"unit-bytes-", b"88"])
            unit.write_bytes(unit_bytes)
            for tool in ("ufw", "systemctl"):
                stub = fakebin / tool
                stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            for name in ("loginctl", "nft"):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--dry-run", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(state.read_bytes(), state_bytes)
            self.assertEqual(unit.read_bytes(), unit_bytes)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_service_cleanup_outcomes(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_case(
            name,
            with_file,
            list_exit,
            list_out,
            disable_exit,
            disable_msg,
            reload_exit,
            want_service,
            want_disable,
            want_reload,
            want_rc,
        ):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    stubdir = tmp_path / "stubs"
                    for p in (home, opos_home, bindir, stubdir):
                        p.mkdir(parents=True)
                    unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                    if with_file:
                        unit.parent.mkdir(parents=True, exist_ok=True)
                        unit.write_text("unit-bytes\n", encoding="utf-8")
                    log = tmp_path / "systemctl.log"
                    log.write_text("", encoding="utf-8")
                    write_stub(
                        stubdir / "systemctl",
                        'LOG="'
                        + str(log)
                        + '"\n'
                        + 'UNIT="'
                        + str(unit)
                        + '"\n'
                        + 'HAS="absent"\n'
                        + 'if [ -f "$UNIT" ]; then HAS="present"; fi\n'
                        + 'printf \'%s|%s\\n\' "$*" "$HAS" >> "$LOG"\n'
                        + 'case "$*" in *list-unit-files*)\n'
                        + "printf '%s' \""
                        + list_out
                        + '"\n'
                        + "exit "
                        + str(list_exit)
                        + ";;\n"
                        + "esac\n"
                        + 'case "$*" in *disable*)\n'
                        + 'if [ "'
                        + disable_msg
                        + '" != "" ]; then printf \'%s\\n\' "'
                        + disable_msg
                        + '" >&2; fi\n'
                        + "exit "
                        + str(disable_exit)
                        + ";;\n"
                        + "esac\n"
                        + 'case "$*" in *daemon-reload*) exit '
                        + str(reload_exit)
                        + ";;\n"
                        + "esac\n"
                        + "exit 0\n",
                    )
                    write_stub(stubdir / "ufw", "exit 0\n")
                    write_stub(stubdir / "id", "printf '1001\\n'\n")
                    env = dict(os.environ)
                    env.pop("SUDO_USER", None)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall", "--force"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, want_rc, proc.stdout + proc.stderr)
                    self.assertIn("service=" + want_service, proc.stdout)
                    calls = log.read_text(encoding="utf-8").splitlines()
                    disables = [c for c in calls if "disable" in c]
                    reloads = [c for c in calls if "daemon-reload" in c]
                    listings = [c for c in calls if "list-unit-files" in c]
                    self.assertEqual(len(listings), 1)
                    if want_disable:
                        self.assertTrue(disables)
                    else:
                        self.assertEqual(disables, [])
                    if want_reload:
                        self.assertTrue(reloads)
                    else:
                        self.assertEqual(reloads, [])
                    if name == "local_unit_success":
                        self.assertTrue(disables and disables[0].endswith("|present"))
                        self.assertTrue(reloads and reloads[0].endswith("|absent"))
                    if name in ("failed_probe", "disable_other_failure"):
                        self.assertTrue(unit.is_file())
                        self.assertEqual(unit.read_bytes(), b"unit-bytes\n")
                    else:
                        self.assertFalse(unit.exists())
                    return proc

        gone_msg = "Failed to disable unit: Unit file opos-server.service does not exist."
        run_case("empty_success_no_file", False, 0, "", 0, "", 0, "skipped", False, False, 0)
        run_case("failed_probe_no_file", False, 1, "", 0, "", 0, "failed", False, False, 1)
        run_case("failed_probe", True, 1, "", 0, "", 0, "failed", False, False, 1)
        run_case("local_unit_success", True, 0, "", 0, "", 0, "succeeded", True, True, 0)
        run_case("listed_no_file", False, 0, "opos-server.service disabled\\n", 0, "", 0, "succeeded", True, True, 0)
        run_case("disable_gone", True, 0, "", 1, gone_msg, 0, "skipped", True, True, 0)
        run_case("disable_other_failure", True, 0, "", 1, "boom", 0, "failed", True, True, 1)
        run_case("reload_failure", True, 0, "", 0, "", 1, "failed", True, True, 1)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_failed_probe_preserves_unit_and_reports_failed(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            for p in (home, opos_home, bindir, stubdir):
                p.mkdir(parents=True)
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            marker = b"".join([b"unit-bytes-", b"31"])
            unit.write_bytes(marker)
            log = tmp_path / "calls.log"
            log.write_text("", encoding="utf-8")
            stub = stubdir / "systemctl"
            stub.write_text(
                "#!/bin/sh\n"
                + 'printf \'%s\\n\' "systemctl $*" >> "'
                + str(log)
                + '"\n'
                + 'case "$*" in *list-unit-files*) exit 1;; esac\n'
                + "exit 0\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            id_stub = stubdir / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("service=failed", proc.stdout)
            self.assertTrue(unit.is_file())
            self.assertEqual(unit.read_bytes(), marker)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any("list-unit-files" in c for c in calls))
            self.assertFalse(any("disable" in c for c in calls))
            self.assertFalse(any("daemon-reload" in c for c in calls))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_failed_probe_without_unit_file_reports_failed(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            for p in (home, opos_home, bindir, stubdir):
                p.mkdir(parents=True)
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            self.assertFalse(unit.exists())
            log = tmp_path / "calls.log"
            log.write_text("", encoding="utf-8")
            stub = stubdir / "systemctl"
            stub.write_text(
                "#!/bin/sh\n"
                + 'printf \'%s\\n\' "systemctl $*" >> "'
                + str(log)
                + '"\n'
                + 'case "$*" in *list-unit-files*) exit 1;; esac\n'
                + "exit 0\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            id_stub = stubdir / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("service=failed", proc.stdout)
            self.assertFalse(unit.exists())
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any("list-unit-files" in c for c in calls))
            self.assertFalse(any("disable" in c for c in calls))
            self.assertFalse(any("daemon-reload" in c for c in calls))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_restore_failures_surface_stderr_and_fail(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        diags = {
            "ufw_reload": "".join(["ufw-", "reload-", "diag-", "31"]),
            "service_disable": "".join(["service-", "disable-", "diag-", "42"]),
            "daemon_reload": "".join(["daemon-", "reload-", "diag-", "43"]),
        }
        wants = {
            "ufw_reload": "ufw reload",
            "service_disable": "systemctl --user disable --now opos-server.service",
            "daemon_reload": "systemctl --user daemon-reload",
        }
        for name in ("ufw_reload", "service_disable", "daemon_reload"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    fakebin = tmp_path / "fakebin"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, fakebin, root):
                        p.mkdir(parents=True)
                    launcher = bindir / "opos"
                    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
                    (opos_home / "manifest").write_text(
                        "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                        encoding="utf-8",
                    )
                    state = opos_home / "".join(["fire", "wall.state"])
                    state.write_bytes(b"inactive\n")
                    unit = home / ".config" / "systemd" / "user" / "opos-server.service"
                    unit.parent.mkdir(parents=True, exist_ok=True)
                    unit.write_bytes(b"".join([b"unit-bytes-", b"5"]))
                    log = tmp_path / "".join(["call", ".log"])
                    log.write_text("", encoding="utf-8")
                    diag = diags[name]
                    ufw_stub = fakebin / "ufw"
                    if name == "ufw_reload":
                        ufw_body = (
                            'printf \'%s\\n\' "ufw $*" >> "'
                            + str(log)
                            + "\"\nprintf '%s\\n' \""
                            + diag
                            + '" >&2\nexit 1\n'
                        )
                    else:
                        ufw_body = 'printf \'%s\\n\' "ufw $*" >> "' + str(log) + '"\nexit 0\n'
                    ufw_stub.write_text("#!/bin/sh\n" + ufw_body, encoding="utf-8")
                    ufw_stub.chmod(ufw_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_stub = fakebin / "systemctl"
                    disable_diag = diags["service_disable"]
                    reload_diag = diags["daemon_reload"]
                    if name == "service_disable":
                        disable_action = "printf '%s\\n' \"" + disable_diag + '" >&2\nexit 1\n'
                    else:
                        disable_action = "exit 0\n"
                    if name == "daemon_reload":
                        reload_action = "printf '%s\\n' \"" + reload_diag + '" >&2\nexit 1\n'
                    else:
                        reload_action = "exit 0\n"
                    ctl_stub.write_text(
                        "#!/bin/sh\n"
                        + 'printf \'%s\\n\' "systemctl $*" >> "'
                        + str(log)
                        + '"\n'
                        + "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                        + 'case "$*" in *disable*) '
                        + disable_action
                        + ";; esac\n"
                        + 'case "$*" in *daemon-reload*) '
                        + reload_action
                        + ";; esac\n"
                        + "exit 0\n",
                        encoding="utf-8",
                    )
                    ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    id_stub = fakebin / "id"
                    id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
                    id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env.pop("SUDO_USER", None)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env)
                    calls = log.read_text(encoding="utf-8").splitlines()
                    self.assertIn(wants[name], calls)
                    self.assertIn(diag, proc.stderr)
                    self.assertNotEqual(proc.returncode, 0)
                    if name == "ufw_reload":
                        self.assertIn("".join(["fire", "wall=failed"]), proc.stdout)
                    else:
                        self.assertIn("service=failed", proc.stdout)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_fallback_routes_restore_privileges(self):
        text = read_install_sh()
        begin_marker = "# BEGIN fallback service cleanup"
        end_marker = "# END fallback service cleanup"
        block_start = text.index(begin_marker) + len(begin_marker)
        block_end = text.index(end_marker, block_start)
        block = text[block_start:block_end]
        alice = "".join(["al", "ice"])
        target_uid = "1001"
        runtime = "".join(["/run/", "user/", target_uid])
        bus = "".join(["unix:path=", runtime, "/bus"])
        cases = [
            ("ordinary", target_uid, "", "1", True),
            ("sudo", "0", alice, "1", True),
            ("sudo_without_user", "0", "", "1", True),
            ("sudo_lookup_fails", "0", alice, "0", True),
            ("ordinary_registered_no_file", target_uid, "", "1", False),
            ("sudo_registered_no_file", "0", alice, "1", False),
        ]
        for name, current_uid, sudo_user, target_ok, with_unit in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    root_home = tmp_path / "".join(["root", "-home"])
                    user_home = tmp_path / "".join(["user", "-home"])
                    root_unit = root_home / ".config" / "systemd" / "user" / "opos-server.service"
                    user_unit = user_home / ".config" / "systemd" / "user" / "opos-server.service"
                    root_unit.parent.mkdir(parents=True)
                    user_unit.parent.mkdir(parents=True)
                    root_bytes = b"".join([b"root-unit-", b"11"])
                    user_bytes = b"".join([b"user-unit-", b"22"])
                    root_unit.write_bytes(root_bytes)
                    if with_unit:
                        user_unit.write_bytes(user_bytes)
                    stubdir = tmp_path / "stubs"
                    stubdir.mkdir()
                    sudo_log = tmp_path / "sudo.log"
                    ctl_log = tmp_path / "systemctl.log"
                    rm_log = tmp_path / "rm.log"

                    def write_stub(path, body):
                        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                    def read_lines(path):
                        if path.is_file():
                            return path.read_text(encoding="utf-8").splitlines()
                        return []

                    write_stub(
                        stubdir / "id",
                        'if [ "$#" -eq 1 ]; then printf \'%s\\n\' "$OPOS_TEST_CURRENT_UID"\nexit 0\nfi\n'
                        + 'if [ "$OPOS_TEST_TARGET_OK" = "1" ]; then printf \'%s\\n\' "$OPOS_TEST_TARGET_UID"\nexit 0\nfi\n'
                        + "exit 1\n",
                    )
                    write_stub(
                        stubdir / "getent",
                        'if [ "$OPOS_TEST_TARGET_OK" != "1" ]; then exit 2\nfi\n'
                        + 'if [ "$1" = "passwd" ]; then printf \'%s:x:%s:1001::%s:/bin/sh\\n\' "$2" "$OPOS_TEST_TARGET_UID" "$OPOS_TEST_USER_HOME"\nexit 0\nfi\n'
                        + "exit 2\n",
                    )
                    write_stub(
                        stubdir / "sudo",
                        'printf \'%s\\n\' "$*" >> "'
                        + str(sudo_log)
                        + '"\n'
                        + 'if [ "$1" != "-u" ]; then exit 1\nfi\n'
                        + 'if [ -z "$2" ]; then exit 1\nfi\n'
                        + "shift 2\n"
                        + 'exec "$@"\n',
                    )
                    write_stub(
                        stubdir / "systemctl",
                        'printf \'%s\\n\' "systemctl $* HOME=$HOME XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-} DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-} LC_ALL=${LC_ALL:-}" >> "'
                        + str(ctl_log)
                        + '"\n'
                        + "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                        + "exit 0\n",
                    )
                    write_stub(
                        stubdir / "rm",
                        'printf \'%s\\n\' "rm $*" >> "'
                        + str(rm_log)
                        + '"\n'
                        + 'for a in "$@"; do case "$a" in -*) ;; "$OPOS_TEST_ROOT_UNIT"|"$OPOS_TEST_USER_UNIT") ;; *) exit 1;; esac; done\n'
                        + 'exec /bin/rm "$@"\n',
                    )
                    runner = tmp_path / "run.sh"
                    runner.write_text(
                        '_service="skipped"\n' + block + "\nprintf '%s' \"$_service\"\n", encoding="utf-8"
                    )
                    env = dict(os.environ)
                    env.pop("SUDO_USER", None)
                    if sudo_user:
                        env["SUDO_USER"] = sudo_user
                    env["HOME"] = str(user_home) if name.startswith("ordinary") else str(root_home)
                    env["PATH"] = str(stubdir) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
                    env["OPOS_TEST_CURRENT_UID"] = current_uid
                    env["OPOS_TEST_TARGET_UID"] = target_uid
                    env["OPOS_TEST_TARGET_OK"] = target_ok
                    env["OPOS_TEST_USER_HOME"] = str(user_home)
                    env["OPOS_TEST_ROOT_UNIT"] = str(root_unit)
                    env["OPOS_TEST_USER_UNIT"] = str(user_unit)
                    proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    sudo_lines = read_lines(sudo_log)
                    ctl_lines = read_lines(ctl_log)
                    rm_lines = read_lines(rm_log)
                    if name in ("ordinary", "ordinary_registered_no_file"):
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertEqual(proc.stdout, "succeeded")
                        self.assertEqual(sudo_lines, [])
                        if with_unit:
                            self.assertEqual(len(ctl_lines), 3)
                            self.assertIn("disable --now opos-server.service", ctl_lines[1])
                            self.assertIn("daemon-reload", ctl_lines[2])
                            self.assertEqual(rm_lines, ["rm -f " + str(user_unit)])
                        else:
                            joined = " ".join(ctl_lines)
                            self.assertIn("disable --now opos-server.service", joined)
                            self.assertIn("daemon-reload", joined)
                        self.assertFalse(user_unit.exists())
                        self.assertEqual(root_unit.read_bytes(), root_bytes)
                    elif name in ("sudo", "sudo_registered_no_file"):
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertEqual(proc.stdout, "succeeded")
                        if with_unit:
                            self.assertEqual(len(sudo_lines), 4, sudo_lines)
                        else:
                            self.assertGreaterEqual(len(sudo_lines), 3, sudo_lines)
                        self.assertIn("systemctl --user list-unit-files --no-legend opos-server.service", sudo_lines[0])
                        self.assertIn("systemctl --user disable --now opos-server.service", sudo_lines[1])
                        self.assertIn("systemctl --user daemon-reload", sudo_lines[-1])
                        if with_unit:
                            self.assertIn("rm -f " + str(user_unit), sudo_lines[2])
                        for line in sudo_lines:
                            self.assertIn("-u " + alice + " ", line)
                            self.assertIn("HOME=" + str(user_home), line)
                            self.assertIn("XDG_RUNTIME_DIR=" + runtime, line)
                            self.assertIn("DBUS_SESSION_BUS_ADDRESS=" + bus, line)
                            self.assertIn("LC_ALL=C", line)
                        if with_unit:
                            self.assertEqual(len(ctl_lines), 3)
                            self.assertEqual(rm_lines, ["rm -f " + str(user_unit)])
                        else:
                            joined = " ".join(ctl_lines)
                            self.assertIn("disable --now opos-server.service", joined)
                            self.assertIn("daemon-reload", joined)
                        self.assertFalse(user_unit.exists())
                        self.assertEqual(root_unit.read_bytes(), root_bytes)
                    else:
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                        self.assertEqual(proc.stdout, "skipped")
                        self.assertIn(
                            "service cleanup skipped: no original non-root user could be resolved", proc.stderr
                        )
                        self.assertEqual(sudo_lines, [])
                        self.assertEqual(ctl_lines, [])
                        self.assertEqual(rm_lines, [])
                        self.assertEqual(root_unit.read_bytes(), root_bytes)
                        self.assertEqual(user_unit.read_bytes(), user_bytes)

    @unittest.skipIf(sys.platform == "win32", "needs the POSIX cut tool")
    def test_service_failure_diagnostics_hide_resolved_home(self):
        text = read_install_sh()
        begin_marker = "# BEGIN fallback service cleanup"
        end_marker = "# END fallback service cleanup"
        block_start = text.index(begin_marker) + len(begin_marker)
        block_end = text.index(end_marker, block_start)
        block = text[block_start:block_end]
        svc_name = "".join(["opos-server", ".service"])
        user_name = "".join(["al", "ice"])
        target_uid = "1001"
        cases = [
            ("ordinary_probe", False, True),
            ("ordinary_missing", False, False),
            ("sudo_probe", True, True),
            ("sudo_missing", True, False),
        ]
        for name, use_sudo, with_ctl in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    caller_home = tmp_path / "".join(["cal", "ler"])
                    target_home = tmp_path / "".join(["tar", "get"])
                    caller_home.mkdir()
                    target_home.mkdir()
                    caller_unit = caller_home / ".config" / "systemd" / "user" / svc_name
                    target_unit = target_home / ".config" / "systemd" / "user" / svc_name
                    caller_unit.parent.mkdir(parents=True, exist_ok=True)
                    target_unit.parent.mkdir(parents=True, exist_ok=True)
                    caller_bytes = b"".join([b"caller-unit-", b"31"])
                    target_bytes = b"".join([b"target-unit-", b"42"])
                    caller_unit.write_bytes(caller_bytes)
                    target_unit.write_bytes(target_bytes)
                    stubdir = tmp_path / "".join(["stu", "bs"])
                    stubdir.mkdir()
                    ctl_log = tmp_path / "".join(["ctl", ".log"])
                    rm_log = tmp_path / "".join(["rm", ".log"])
                    ctl_log.write_text("", encoding="utf-8")
                    rm_log.write_text("", encoding="utf-8")

                    def write_stub(path, body):
                        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                    current_uid = "0" if use_sudo else target_uid
                    write_stub(
                        stubdir / "id",
                        'if [ "$#" -eq 0 ]; then printf \'%s\\n\' "$OPOS_CURRENT_UID"\nexit 0\nfi\n'
                        + 'if [ "$#" -eq 1 ]; then printf \'%s\\n\' "'
                        + target_uid
                        + '"\nexit 0\nfi\nexit 1\n',
                    )
                    write_stub(
                        stubdir / "getent",
                        'if [ "$1" = "passwd" ]; then printf \'%s:x:%s:1001::%s:/bin/sh\\n\' "$2" "'
                        + target_uid
                        + '" "$OPOS_TARGET_HOME"\nexit 0\nfi\nexit 2\n',
                    )
                    write_stub(
                        stubdir / "sudo",
                        'if [ "$1" != "-u" ]; then exit 1\nfi\nshift 2\nexec "$@"\n',
                    )
                    if with_ctl:
                        write_stub(
                            stubdir / "systemctl",
                            'printf \'%s\\n\' "systemctl $*" >> "'
                            + str(ctl_log)
                            + '"\n'
                            + 'case "$*" in *list-unit-files*) exit 1;; esac\nexit 0\n',
                        )
                    write_stub(
                        stubdir / "rm",
                        'printf \'%s\\n\' "rm $*" >> "' + str(rm_log) + '"\nexec /bin/rm "$@"\n',
                    )
                    for tool in ("cut", "grep", "env"):
                        found = None
                        for entry in os.environ.get("PATH", "").split(os.pathsep):
                            cand = os.path.join(entry, tool)
                            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                                found = cand
                                break
                        self.assertIsNotNone(found, tool)
                        os.symlink(found, str(stubdir / tool))
                    runner = tmp_path / "run.sh"
                    runner.write_text(
                        '_service="skipped"\n' + block + "\nprintf '%s' \"$_service\"\n", encoding="utf-8"
                    )
                    env = dict(os.environ)
                    env.pop("SUDO_USER", None)
                    if use_sudo:
                        env["SUDO_USER"] = user_name
                    env["HOME"] = str(caller_home)
                    env["PATH"] = str(stubdir)
                    env["OPOS_CURRENT_UID"] = current_uid
                    env["OPOS_TARGET_HOME"] = str(target_home)
                    proc = subprocess.run(["/bin/sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(proc.stdout, "failed", name)
                    if with_ctl:
                        want = "".join(["systemd probe failed; preserving "]) + svc_name
                    else:
                        want = "".join(["systemctl not found; preserving "]) + svc_name
                    self.assertIn(want, proc.stderr.splitlines(), name)
                    for leak in (str(caller_home), str(target_home), str(caller_unit), str(target_unit)):
                        self.assertNotIn(leak, proc.stdout, name)
                        self.assertNotIn(leak, proc.stderr, name)
                    self.assertEqual(caller_unit.read_bytes(), caller_bytes, name)
                    self.assertEqual(target_unit.read_bytes(), target_bytes, name)
                    calls = ctl_log.read_text(encoding="utf-8")
                    self.assertNotIn("disable", calls, name)
                    self.assertNotIn("daemon-reload", calls, name)
                    rm_calls = rm_log.read_text(encoding="utf-8")
                    self.assertEqual(rm_calls, "", name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_missing_systemctl_preserves_unit_and_reports_failed(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            farm = tmp_path / "farm"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, farm, root):
                p.mkdir(parents=True)
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_dir = opos_home / "venv"
            venv_dir.mkdir(parents=True)
            (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
            (opos_home / "manifest").write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            marker = b"".join([b"unit-bytes-", b"91"])
            unit.write_bytes(marker)
            seen = set()
            for entry in os.environ.get("PATH", "").split(os.pathsep):
                if not entry or not os.path.isdir(entry):
                    continue
                for name in os.listdir(entry):
                    if name == "systemctl" or name in seen:
                        continue
                    src = os.path.join(entry, name)
                    if os.path.isfile(src) and os.access(src, os.X_OK):
                        try:
                            os.symlink(src, farm / name)
                        except FileExistsError:
                            pass
                        seen.add(name)
            id_link = farm / "id"
            if id_link.is_symlink() or id_link.exists():
                id_link.unlink()
            id_stub = farm / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(farm)
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("service=failed", proc.stdout)
            self.assertTrue(unit.is_file())
            self.assertEqual(unit.read_bytes(), marker)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_failed_firewall_restore_preserves_state_for_retry(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_name = "".join(["fire", "wall.state"])
        failed_mark = "".join(["fire", "wall=failed"])
        ok_mark = "".join(["fire", "wall=succeeded"])
        want_args = "".join(["--", "force disable"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, stubdir, root):
                p.mkdir(parents=True)
            sentinel = b"inactive\n"
            state = opos_home / state_name
            state.write_bytes(sentinel)
            self.assertFalse((bindir / "opos").exists())
            self.assertFalse((opos_home / "manifest").exists())
            log = tmp_path / "ufw.log"
            log.write_text("", encoding="utf-8")
            ufw = stubdir / "ufw"
            ufw.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(log) + '"\nexit 1\n', encoding="utf-8")
            ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl = stubdir / "systemctl"
            ctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl.chmod(ctl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn(failed_mark, proc.stdout)
            self.assertIn(want_args, log.read_text(encoding="utf-8"))
            self.assertEqual(state.read_bytes(), sentinel)
            ufw.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(log) + '"\nexit 0\n', encoding="utf-8")
            ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            proc = subprocess.run(
                ["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn(ok_mark, proc.stdout)
            self.assertFalse(state.exists(), "state file must be removed after successful retry")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_disabled_firewall_requires_exact_saved_state(self):
        text = read_install_sh()
        leaf = "".join(["fire", "wall.state"])
        anchor = 'if [ -f "$OPOS_HOME/' + leaf + '" ]'
        start = text.index(anchor)
        line_start = text.rindex("\n", 0, start) + 1
        tail = text[line_start:].splitlines(keepends=True)
        depth = 0
        used = 0
        for line in tail:
            stripped = line.strip()
            if stripped.startswith("if ") or stripped.startswith("if\t"):
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
                depth -= 1
                used += len(line)
                if depth == 0:
                    break
                continue
            used += len(line)
        block = text[line_start : line_start + used]
        self.assertIn(leaf, block)
        valid = "".join(["in", "active"]) + "\n"
        self.assertEqual(valid, "inactive\n")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            opos_home = tmp_path / "opos"
            opos_home.mkdir()
            saved = opos_home / leaf
            saved.write_bytes(valid.encode())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            log = tmp_path / "ufw.log"
            stub = stubdir / "ufw"
            stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(log) + '"\nexit 0\n', encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            script = (
                'OPOS_HOME="' + str(opos_home) + '"\n_firewall="skipped"\n' + block + "\nprintf '%s' \"$_firewall\"\n"
            )
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "succeeded", "exact disabled state must restore")
            self.assertFalse(saved.exists(), "exact disabled state must be consumed")
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertIn("".join(["--", "force disable"]), calls)
            self.assertIn("reload", calls)
        tricky = [
            "".join(["in", "active"]) + " \n",
            " " + "".join(["in", "active"]) + "\n",
            "".join(["IN", "ACTIVE"]) + "\n",
            "".join(["in", "active"]) + "\n" + "".join(["ex", "tra"]) + "\n",
            "".join(["of", "f"]) + " \n",
        ]
        for idx, data in enumerate(tricky):
            with self.subTest(case=idx):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    before = data.encode()
                    saved = opos_home / leaf
                    saved.write_bytes(before)
                    stubdir = tmp_path / "stubs"
                    stubdir.mkdir()
                    log = tmp_path / "ufw.log"
                    stub = stubdir / "ufw"
                    stub.write_text(
                        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(log) + '"\nexit 0\n', encoding="utf-8"
                    )
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    script = (
                        'OPOS_HOME="'
                        + str(opos_home)
                        + '"\n_firewall="skipped"\n'
                        + block
                        + "\nprintf '%s' \"$_firewall\"\n"
                    )
                    runner = tmp_path / "run.sh"
                    runner.write_text(script, encoding="utf-8")
                    env = dict(os.environ)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout, "failed", "near-disabled snapshot must not restore")
                    self.assertEqual(saved.read_bytes(), before, "malformed snapshot must be preserved")
                    self.assertFalse(log.exists(), "malformed snapshot must make no UFW calls")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_replacement_restores_disabled_firewall_and_expected_service(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        fw_leaf = "".join(["fire", "wall.state"])
        fw_ok = "".join(["fire", "wall=succeeded"])
        svc_ok = "service=succeeded"
        want_fw = "".join(["--", "force disable"])
        want_reload = "reload"
        want_disable = "disable --now opos-server.service"
        want_daemon = "daemon-reload"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            fakebin = tmp_path / "fakebin"
            root = tmp_path / "root"
            for p in (home, opos_home, bindir, fakebin, root):
                p.mkdir(parents=True)
            launcher = bindir / "opos"
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            venv_dir = opos_home / "venv"
            venv_dir.mkdir(parents=True)
            (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
            (opos_home / "manifest").write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            fw_state = opos_home / fw_leaf
            fw_state.write_bytes(b"inactive\n")
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_bytes(b"".join([b"unit-bytes-", b"32"]))
            ufw_log = tmp_path / "ufw.log"
            ufw_log.write_text("", encoding="utf-8")
            ctl_log = tmp_path / "ctl.log"
            ctl_log.write_text("", encoding="utf-8")
            ufw_stub = fakebin / "ufw"
            ufw_stub.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(ufw_log) + '"\nexit 0\n',
                encoding="utf-8",
            )
            ufw_stub.chmod(ufw_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "'
                + str(ctl_log)
                + '"\n'
                + "case \"$*\" in *list-unit-files*) printf 'opos-server.service enabled\\n'\nexit 0;; esac\n"
                + 'case "$*" in *disable*) exit 0;; esac\n'
                + 'case "$*" in *daemon-reload*) exit 0;; esac\n'
                + "exit 0\n",
                encoding="utf-8",
            )
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            for name in ("loginctl", "nft"):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
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
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
            self.assertNotEqual(line, "")
            self.assertIn(fw_ok, line)
            self.assertIn(svc_ok, line)
            logged_ufw = ufw_log.read_text(encoding="utf-8")
            self.assertIn(want_fw, logged_ufw)
            self.assertIn(want_reload, logged_ufw)
            logged_ctl = ctl_log.read_text(encoding="utf-8")
            self.assertIn(want_disable, logged_ctl)
            self.assertIn(want_daemon, logged_ctl)
            self.assertFalse(fw_state.exists())
            self.assertFalse(unit.exists())

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_fallback_invalid_firewall_state_preserves_recovery_evidence(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_leaf = "".join(["fire", "wall.state"])
        failed_mark = "".join(["fire", "wall=failed"])
        diag_mark = "".join(["invalid saved fire", "wall state"])
        for name, data in (
            ("empty", b""),
            ("unknown", b"not-tracked"),
            ("space", b" \n"),
        ):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    stubdir = tmp_path / "stubs"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, stubdir, root):
                        p.mkdir(parents=True)
                    state = opos_home / state_leaf
                    state.write_bytes(data)
                    log = tmp_path / "ufw.log"
                    ufw = stubdir / "ufw"
                    ufw.write_text(
                        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(log) + '"\nexit 0\n',
                        encoding="utf-8",
                    )
                    ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl_log = tmp_path / "ctl.log"
                    ctl = stubdir / "systemctl"
                    ctl.write_text(
                        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(ctl_log) + '"\nexit 0\n',
                        encoding="utf-8",
                    )
                    ctl.chmod(ctl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = stubdir / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    self.assertFalse((bindir / "opos").exists())
                    self.assertFalse((opos_home / "manifest").exists())
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn(failed_mark, proc.stdout, name)
                    self.assertIn(diag_mark, proc.stderr, name)
                    self.assertIn(str(state), proc.stderr, name)
                    self.assertFalse(log.exists(), "no UFW calls allowed")
                    self.assertTrue(opos_home.is_dir(), name)
                    self.assertEqual(state.read_bytes(), data, name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_fallback_firewall_respects_recorded_state(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_leaf = "".join(["fire", "wall.state"])
        ok_mark = "".join(["fire", "wall=succeeded"])
        skip_mark = "".join(["fire", "wall=skipped"])
        disable_call = "".join(["--", "force disable"])
        cases = (
            ("missing", None, skip_mark, []),
            ("active", b"active\n", ok_mark, []),
            ("on", b"on\n", ok_mark, []),
            ("inactive", b"inactive\n", ok_mark, [disable_call, "reload"]),
            ("off", b"off\n", ok_mark, [disable_call, "reload"]),
        )
        for name, data, want_mark, want_calls in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos_home = tmp_path / "opos"
                    bindir = tmp_path / "bin"
                    stubdir = tmp_path / "stubs"
                    root = tmp_path / "root"
                    for p in (home, opos_home, bindir, stubdir, root):
                        p.mkdir(parents=True)
                    state = opos_home / state_leaf
                    if data is not None:
                        state.write_bytes(data)
                    log = tmp_path / "ufw.log"
                    ufw = stubdir / "ufw"
                    ufw.write_text(
                        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(log) + '"\nexit 0\n',
                        encoding="utf-8",
                    )
                    ufw.chmod(ufw.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ctl = stubdir / "systemctl"
                    ctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    ctl.chmod(ctl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("loginctl", "nft"):
                        stub = stubdir / tool
                        stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    self.assertFalse((bindir / "opos").exists())
                    self.assertFalse((opos_home / "manifest").exists())
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
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
                    self.assertIn(want_mark, proc.stdout, name)
                    self.assertFalse(state.exists(), name)
                    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
                    self.assertEqual(calls, want_calls, name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_force_uninstall_targets_real_service(self):
        text = read_install_sh()
        block = uninstall_restore_block(text)
        begin_marker = "# BEGIN fallback service cleanup"
        service = block[block.index(begin_marker) :]
        self.assertIn("opos-server.service", service)
        self.assertNotIn("ufw", service)
        self.assertNotIn("sshd -t", service)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            unit_dir = home / ".config" / "systemd" / "user"
            unit_dir.mkdir(parents=True)
            unit = unit_dir / "opos-server.service"
            keep = unit_dir / "keep.service"
            unit_bytes = b"".join([b"unit-", b"real-", b"1"])
            keep_bytes = b"".join([b"unit-", b"keep-", b"2"])
            unit.write_bytes(unit_bytes)
            keep.write_bytes(keep_bytes)
            stubdir = tmp_path / "stub"
            stubdir.mkdir()
            log = tmp_path / "calls.log"
            stub = stubdir / "systemctl"
            stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(log) + '"\nexit 0\n', encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            id_stub = stubdir / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            script = "_sshd=skipped\n_firewall=skipped\n_service=skipped\n" + service
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                [
                    "--user list-unit-files --no-legend opos-server.service",
                    "--user disable --now opos-server.service",
                    "--user daemon-reload",
                ],
            )
            self.assertFalse(unit.exists())
            self.assertEqual(keep.read_bytes(), keep_bytes)
            self.assertIn("sshd=skipped firewall=skipped service=succeeded", proc.stdout)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_uninstall_preserves_unverified_service_unit(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            stubdir = tmp_path / "stubs"
            for p in (home, opos_home, bindir, stubdir):
                p.mkdir(parents=True)
            target = tmp_path / "".join(["exter", "nal-target"])
            target_bytes = b"".join([b"foreign-", b"unit-", b"9"])
            target.write_bytes(target_bytes)
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(target), str(unit))
            before_link = os.readlink(str(unit))
            log = tmp_path / "systemctl.log"
            log.write_text("", encoding="utf-8")
            stub = stubdir / "systemctl"
            stub.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "'
                + str(log)
                + '"\n'
                + "case \"$*\" in *list-unit-files*) printf 'opos-server.service enabled\\n'\nexit 0;; esac\n"
                + "exit 0\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            id_stub = stubdir / "id"
            id_stub.write_text("#!/bin/sh\nprintf '1001\\n'\n", encoding="utf-8")
            id_stub.chmod(id_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(install), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("service=failed", proc.stdout)
            self.assertTrue(unit.is_symlink(), "unverified symlink unit must be preserved")
            self.assertEqual(os.readlink(str(unit)), before_link)
            self.assertEqual(target.read_bytes(), target_bytes)
            calls = log.read_text(encoding="utf-8")
            self.assertNotIn("disable", calls)


if __name__ == "__main__":
    unittest.main()
