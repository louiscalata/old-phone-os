# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from tests import install_review_support as review
from tests.install_review_support import (
    _EXPECTED_BACKUP_PROBE,
    _assert_snippet_confined,
    _open_symlink_fd,
    _prepare_fallback_restore_snippet,
    _read_install_sh,
    _run_sshd_dropin_restore,
    _uninstall_restore_block,
    _writes_backup,
)


class TestInstallReview(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_f992_sshd_leaves_unverified_customized_active_config(self):
        text = _read_install_sh()
        uninstall = text.index("do_uninstall() {")
        sshd_start = text.index('    _sshd="skipped"', uninstall)
        fw_leaf = "".join(["fire", "wall.state"])
        fw_anchor = 'if [ -f "$OPOS_HOME/' + fw_leaf + '"'
        fw_idx = text.index(fw_anchor, sshd_start)
        line_start = text.rindex("\n", 0, fw_idx) + 1
        body = text[sshd_start:line_start]
        prod_prefix = "/etc/" + "ssh/"
        self.assertIn(prod_prefix, body)
        cases = ("missing_expected", "mismatched_expected")
        for name in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    sshdir = tmp_path / "ssh"
                    dropin_dir = sshdir / ("sshd_config" + ".d")
                    dropin_dir.mkdir(parents=True)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    admin_user = "".join(["admin@", "example.", "invalid"])
                    marker = "# Managed by " + "OPOS - sshd drop-in\n"
                    active_dropin = dropin_dir / "60-opos.conf"
                    active_dropin.write_bytes((marker + "Port 2222\nAllowUsers " + admin_user + "\n").encode())
                    dest = sshdir / ("sshd_" + "config")
                    dest.write_bytes(b"Port 2222\n")
                    bak = opos_home / ("sshd_config" + ".bak")
                    bak.write_bytes(b"Port 22\n")
                    expected = opos_home / "sshd_dropin.expected"
                    if name == "mismatched_expected":
                        expected.write_bytes((marker + "Port 2222\n").encode())
                    self.assertFalse(expected.is_file() and name == "missing_expected")
                    before_dropin = active_dropin.read_bytes()
                    before_dest = dest.read_bytes()
                    before_bak = bak.read_bytes()
                    script = body.replace(prod_prefix, str(sshdir) + "/")
                    self.assertNotIn(prod_prefix, script)
                    script += '\nprintf "%s" "$_sshd"\n'
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(opos_home)
                    env["HOME"] = str(tmp_path)
                    proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(active_dropin.read_bytes(), before_dropin, name)
                    self.assertEqual(dest.read_bytes(), before_dest, name)
                    self.assertEqual(bak.read_bytes(), before_bak, name)
                    self.assertEqual(proc.stdout, "failed", name)
                    self.assertIn("manual sshd drop-in restoration required", proc.stderr, name)
                    self.assertIn(str(active_dropin), proc.stderr, name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_restore_steps_report_failures_and_preserve_sshd_backup(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        start = text.index("FOUND=0", anchor)
        stop = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        end = text.index(stop)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest.write_bytes(b"".join([b"dest-bytes-", b"31"]))
            sentinel = b"".join([b"bak-bytes-", b"42"])
            bak = opos_home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(sentinel)
            state = opos_home / "".join(["fire", "wall.state"])
            state.write_bytes(b"inactive\n")
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            markers = {}
            for tool in ("cp", "ufw"):
                marker = tmp_path / (tool + ".invoked")
                markers[tool] = marker
                stub = stubdir / tool
                stub.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n', encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            marker = tmp_path / "systemctl.invoked"
            markers["systemctl"] = marker
            stub = stubdir / "systemctl"
            stub.write_text(
                '#!/bin/sh\ntouch "'
                + str(marker)
                + '"\n'
                + "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'; exit 0;; esac\n"
                + "exit 1\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            stat_stub = stubdir / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_stub = stubdir / "sshd"
            sshd_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = fragment.replace(dest_literal, str(dest))
            preamble = (
                "is_hardening_active() { return 1; }\n"
                + "canon_repo() { printf '%s' \"$1\"; }\n"
                + "print_hardening_instructions() { :; }\n"
                + "opos_owns_linger() { return 1; }\n"
                + "is_linger_enabled() { return 1; }\n"
            )
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["DRY"] = "0"
            env["UVENV"] = ""
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for tool, marker in markers.items():
                self.assertTrue(marker.is_file(), tool + " stub must run")
            self.assertIn("==> restore summary: sshd=failed firewall=failed service=failed", proc.stdout.splitlines())
            self.assertEqual(bak.read_bytes(), sentinel)
            self.assertEqual(dest.read_bytes(), b"".join([b"dest-bytes-", b"31"]))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_fallback_preserves_prior_firewall_failure(self):
        text = _read_install_sh()
        leaf = "".join(["fire", "wall.state"])
        fail_mark = "".join(["fire", "wall=failed"])
        ok_mark = "".join(["fire", "wall=succeeded"])
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
        self.assertIn("==> restore summary", text)
        for state in ("active", "on", "inactive", "off"):
            with self.subTest(state=state, mode="preserve"):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    before = (state + "\n").encode()
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
                        + '"\n_firewall="failed"\n'
                        + block
                        + "\nprintf '%s' \"$_firewall\"\n"
                    )
                    runner = tmp_path / "run.sh"
                    runner.write_text(script, encoding="utf-8")
                    env = dict(os.environ)
                    env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(proc.stdout, "failed", state)
                    self.assertEqual(saved.read_bytes(), before, state)
                    self.assertFalse(log.exists(), state + " must make no UFW calls")
        for state in ("active", "on", "inactive", "off"):
            with self.subTest(state=state, mode="control"):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    saved = opos_home / leaf
                    saved.write_bytes((state + "\n").encode())
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
                    self.assertEqual(proc.stdout, "succeeded", state)
                    self.assertFalse(saved.exists(), state)
                    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
                    if state in ("active", "on"):
                        self.assertEqual(calls, [], state)
                    else:
                        self.assertIn("".join(["--", "force disable"]), calls, state)
                        self.assertIn("reload", calls, state)
        self.assertTrue(fail_mark is not None)
        self.assertTrue(ok_mark is not None)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_restore_reports_skipped_sshd_and_retains_backup(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        start = text.index("FOUND=0", anchor)
        stop = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        end = text.index(stop)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        dropin_literal = "/etc/ssh/" + "sshd_config.d/60-opos.conf"
        self.assertIn(dest_literal, fragment)
        self.assertIn("==> restore summary", fragment)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest_bytes = b"".join([b"dest-bytes-", b"31"])
            dest.write_bytes(dest_bytes)
            sentinel = b"".join([b"bak-bytes-", b"43"])
            keep = opos_home / "keep"
            keep.write_bytes(sentinel)
            bak = opos_home / "".join(["sshd_config", ".bak"])
            state = opos_home / "".join(["fire", "wall.state"])
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            self.assertFalse(bak.exists())
            self.assertFalse(state.exists())
            self.assertFalse(unit.exists())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            markers = {}
            for tool in ("cp", "ufw", "stat", "sshd", "mv"):
                marker = tmp_path / (tool + ".invoked")
                markers[tool] = marker
                stub = stubdir / tool
                stub.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n', encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_marker = tmp_path / "systemctl.invoked"
            ctl_stub = stubdir / "systemctl"
            ctl_stub.write_text(
                '#!/bin/sh\ntouch "' + str(ctl_marker) + '"\ncase "$*" in *list-unit-files*) exit 0;; esac\nexit 1\n',
                encoding="utf-8",
            )
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = fragment.replace(dest_literal, str(dest))
            replaced = replaced.replace(dropin_literal, str(tmp_path / "no-dropin"))
            preamble = (
                "is_hardening_active() { return 1; }\n"
                + "canon_repo() { printf '%s' \"$1\"; }\n"
                + "print_hardening_instructions() { :; }\n"
                + "opos_owns_linger() { return 1; }\n"
                + "is_linger_enabled() { return 1; }\n"
            )
            checks = (
                '\n[ "$_sshd" = "skipped" ] || exit 1\n'
                + '[ "$_firewall" = "skipped" ] || exit 1\n'
                + '[ "$_service" = "skipped" ] || exit 1\n'
                + '[ "$_restore_failed" = "0" ] || exit 1\n'
            )
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced + checks, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["DRY"] = "0"
            env["UVENV"] = ""
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(
                "==> restore summary: sshd=skipped firewall=skipped service=skipped", proc.stdout.splitlines()
            )
            self.assertTrue(ctl_marker.is_file())
            for tool, marker in markers.items():
                self.assertFalse(marker.exists(), tool + " must not run when restoration is skipped")
            self.assertEqual(dest.read_bytes(), dest_bytes)
            self.assertEqual(keep.read_bytes(), sentinel)
            self.assertFalse(bak.exists())
            self.assertFalse(state.exists())
            self.assertFalse(unit.exists())

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_restore_skipped_sshd_preserves_existing_backup(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        start = text.index("FOUND=0", anchor)
        stop = 'opos_owned_path "$LAUNCHER" "$DEF_LAUNCHER" || return 1'
        end = text.index(stop)
        fragment = text[start:end]
        dest_literal = "/etc/ssh/" + "sshd_config"
        dropin_literal = "/etc/ssh/" + "sshd_config.d/60-opos.conf"
        self.assertIn(dest_literal, fragment)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest_before = b"".join([b"dest-bytes-", b"55"])
            dest.write_bytes(dest_before)
            sentinel = b"".join([b"bak-bytes-", b"44"])
            bak = opos_home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(sentinel)
            self.assertFalse((opos_home / "".join(["fire", "wall.state"])).exists())
            self.assertFalse((home / ".config" / "systemd" / "user" / "opos-server.service").exists())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            markers = {}
            for tool in ("cp", "ufw", "mv"):
                marker = tmp_path / (tool + ".invoked")
                markers[tool] = marker
                stub = stubdir / tool
                stub.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n', encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            stat_stub = stubdir / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_stub = stubdir / "sshd"
            sshd_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_marker = tmp_path / "systemctl.invoked"
            ctl_stub = stubdir / "systemctl"
            ctl_stub.write_text(
                '#!/bin/sh\ntouch "' + str(ctl_marker) + '"\ncase "$*" in *list-unit-files*) exit 0;; esac\nexit 1\n',
                encoding="utf-8",
            )
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = fragment.replace(dest_literal, str(dest))
            replaced = replaced.replace(dropin_literal, str(tmp_path / "no-dropin"))
            preamble = (
                "is_hardening_active() { return 1; }\n"
                + "canon_repo() { printf '%s' \"$1\"; }\n"
                + "print_hardening_instructions() { :; }\n"
                + "opos_owns_linger() { return 1; }\n"
                + "is_linger_enabled() { return 1; }\n"
            )
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["DRY"] = "0"
            env["UVENV"] = ""
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("==> restore summary: sshd=failed firewall=skipped service=skipped", proc.stdout.splitlines())
            self.assertTrue(ctl_marker.is_file())
            self.assertFalse(markers["ufw"].exists(), "firewall must stay skipped")
            self.assertFalse(markers["mv"].exists(), "failed sshd must not move staged file")
            self.assertEqual(dest.read_bytes(), dest_before)
            self.assertEqual(bak.read_bytes(), sentinel)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_restore_block_snippet_restores_trusted_backup(self):
        """Run only the extracted restore snippet, not install.sh --uninstall.

        The real uninstall refuses any sshd_config.bak since F297
        (do_uninstall returns 1 with "manual SSH restoration required"), so
        this snippet is kept as a unit check of the restore logic.
        """
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
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
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            opos_home = tmp_path / "opos"
            opos_home.mkdir()
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            bak = opos_home / "".join(["sshd_config", ".bak"])
            bak_bytes = b"".join([b"bak-bytes-", b"71"])
            bak.write_bytes(bak_bytes)
            live_bytes = b"".join([b"dest-bytes-", b"72"])
            dest = tmp_path / "".join(["sshd", "_config"])
            dest.write_bytes(live_bytes)
            stat_stub = fakebin / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_log = tmp_path / "".join(["sshd", ".log"])
            sshd_log.write_text("", encoding="utf-8")
            sshd_stub = fakebin / "sshd"
            sshd_stub.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "' + str(sshd_log) + '"\nexit 0\n', encoding="utf-8"
            )
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = block.replace("/etc/ssh/" + "sshd_config", str(dest))
            dropin = tmp_path / "no-dropin"
            script = '_sshd="skipped"\n'
            script += '_sshd_dropin="' + str(dropin) + '"\n'
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(opos_home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "succeeded")
            self.assertEqual(dest.read_bytes(), bak_bytes)
            self.assertFalse(bak.exists())
            self.assertFalse(bak.is_symlink())
            logged = sshd_log.read_text(encoding="utf-8").splitlines()
            staged = [line for line in logged if "-t" in line.split() and "-f" in line.split()]
            self.assertTrue(staged)
            checked = staged[-1].rsplit(" ", 1)[-1]
            self.assertNotEqual(checked, str(dest))
            self.assertNotEqual(checked, str(bak))
            self.assertEqual(os.path.dirname(checked), str(dest.parent))
            self.assertFalse(os.path.lexists(checked))

    def test_trusted_backup_snippet_test_is_labelled_snippet_only(self):
        for module in review._install_review_modules():
            self.assertFalse(
                hasattr(module.TestInstallReview, "test_uninstall_trusted_sshd_backup_restores_successfully")
            )
        renamed = getattr(TestInstallReview, "test_sshd_restore_block_snippet_restores_trusted_backup", None)
        self.assertIsNotNone(renamed)
        self.assertIn("snippet", renamed.__doc__)
        self.assertIn("F297", renamed.__doc__)

    def test_uninstall_preserves_symlink_backup_targets(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        dest_literal = "/etc/ssh/" + "sshd_config"
        bak_name = "".join(["sshd_config", ".bak"])
        for kind in ("symlink", "dangling"):
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
                    venv_dir = opos_home / "venv"
                    venv_dir.mkdir(parents=True)
                    (venv_dir / "".join(["dum", "my"])).write_bytes(b"".join([b"dummy-venv-", b"1"]))
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
                    dest = tmp_path / "sshd_config"
                    dest.write_bytes(b"".join([b"dest-bytes-", b"31"]))
                    dest_before = dest.read_bytes()
                    bak = opos_home / bak_name
                    real = tmp_path / "real"
                    missing = tmp_path / "".join(["missing-", "target"])
                    if kind == "symlink":
                        real.write_bytes(b"".join([b"bak-bytes-", b"42"]))
                        os.symlink(str(real), str(bak))
                    else:
                        os.symlink(str(missing), str(bak))
                        self.assertFalse(bak.exists())
                    self.assertTrue(bak.is_symlink())
                    before_link = os.readlink(str(bak))
                    before_stat = os.lstat(str(bak))
                    link_fd = _open_symlink_fd(str(bak))
                    real_before = real.read_bytes() if kind == "symlink" else None
                    if kind == "dangling":
                        self.assertFalse(os.path.lexists(str(missing)))
                    script = tmp_path / "install-copy.sh"
                    script.write_bytes(install.read_bytes().replace(dest_literal.encode(), str(dest).encode()))
                    for tool in ("ufw", "systemctl"):
                        stub = fakebin / tool
                        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
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
                    self.assertTrue(bak.is_symlink(), "backup link must remain a symlink")
                    self.assertEqual(os.readlink(str(bak)), before_link, "backup link target must remain unchanged")
                    after_stat = os.lstat(str(bak))
                    self.assertEqual(
                        after_stat.st_ino,
                        before_stat.st_ino,
                        "backup link must keep identity (unlink-and-recreate with same target must fail)",
                    )
                    self.assertEqual(after_stat.st_dev, before_stat.st_dev, "backup link device must be preserved")
                    self.assertEqual(after_stat.st_mode, before_stat.st_mode, "backup link metadata must be preserved")
                    if kind == "symlink":
                        self.assertEqual(
                            real.read_bytes(), real_before, "existing link target must retain original bytes"
                        )
                    else:
                        self.assertFalse(os.path.lexists(str(missing)), "dangling target must remain absent")
                        self.assertFalse(bak.exists())
                    try:
                        orig_stat = os.fstat(link_fd)
                        self.assertEqual(orig_stat.st_ino, before_stat.st_ino)
                        self.assertEqual(orig_stat.st_dev, before_stat.st_dev)
                        after_stat = os.lstat(str(bak))
                        self.assertEqual(after_stat.st_ino, orig_stat.st_ino)
                        self.assertEqual(after_stat.st_dev, orig_stat.st_dev)
                        self.assertEqual(after_stat.st_mode, orig_stat.st_mode)
                        self.assertEqual(os.readlink(str(bak)), before_link)
                    finally:
                        os.close(link_fd)
                    self.assertEqual(dest.read_bytes(), dest_before)

    def test_symlink_identity_holds_original_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            target = tmp_path / "target"
            target.write_bytes(b"".join([b"ta", b"rget-1"]))
            link = tmp_path / "link"
            os.symlink(str(target), str(link))
            held_fd = _open_symlink_fd(str(link))
            try:
                orig = os.fstat(held_fd)
                saved_ino = os.lstat(str(link)).st_ino
                saved_dev = os.lstat(str(link)).st_dev
                self.assertEqual(orig.st_ino, saved_ino)
                self.assertEqual(orig.st_dev, saved_dev)
                os.unlink(str(link))
                os.symlink(str(target), str(link))
                after = os.lstat(str(link))
                held = os.fstat(held_fd)
                self.assertEqual(held.st_ino, saved_ino)
                self.assertEqual(held.st_dev, saved_dev)
                self.assertEqual(held.st_nlink, 0)
                self.assertEqual(after.st_nlink, 1)
                self.assertNotEqual((after.st_dev, after.st_ino), (held.st_dev, held.st_ino))
            finally:
                os.close(held_fd)

    def test_symlink_handle_requires_supported_mechanism(self):
        src = pathlib.Path(review.__file__).read_text(encoding="utf-8")
        needle = "getattr(os, " + '"O_SYMLINK", 0)'
        self.assertNotIn(needle, src, "must not fall back to opening the link target")

        class _FakeOs:
            pass

        def _make_fake(with_path, with_link):
            fake = _FakeOs()
            fake.O_RDONLY = 0
            fake.O_NOFOLLOW = 128
            if with_path:
                fake.O_PATH = 64
            if with_link:
                fake.O_SYMLINK = 256
            fake.open = unittest.mock.Mock(return_value=99)
            return fake

        with unittest.mock.patch("tests.install_review_support.os", _make_fake(True, True)) as fake:
            self.assertEqual(review._open_symlink_fd("dummy"), 99)
            fake.open.assert_called_once_with("dummy", 64 | 128)
        with unittest.mock.patch("tests.install_review_support.os", _make_fake(False, True)) as fake:
            self.assertEqual(review._open_symlink_fd("dummy"), 99)
            fake.open.assert_called_once_with("dummy", 0 | 256)
        with unittest.mock.patch("tests.install_review_support.os", _make_fake(False, False)):
            with self.assertRaises(unittest.SkipTest):
                review._open_symlink_fd("dummy")

    def test_restore_commands_preserve_stderr(self):
        text = _read_install_sh()
        restore_block = _uninstall_restore_block(text)
        self.assertNotIn("2>/dev/null", restore_block)

    def test_restore_block_ignores_unrelated_anchors(self):
        marker = "".join(["unique-restore-", "marker-7"])
        prefix = 'UH="$OPOS_HOME"\nrmdir "$OPOS_BIN_DIR"\n'
        header = "do_uninstall() {\n"
        body = '  echo "' + marker + '"\n  printf \'==> restore summary: %s\\n\' "done"\n'
        suffix = '  cmd || true\n  rmdir "$OPOS_BIN_DIR"\n}\n'
        text = prefix + header + body + suffix
        want = header + body
        self.assertEqual(_uninstall_restore_block(text), want)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_cmp_preserves_error_diagnostics(self):
        text = _read_install_sh()
        restore_block = _uninstall_restore_block(text)
        self.assertNotIn("cmp -s", restore_block)
        self.assertRegex(restore_block, r'cmp "[^"]+" "[^"]+" >/dev/null')
        self.assertNotIn("2>/dev/null", restore_block)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            first = tmp_path / "first"
            second = tmp_path / "second"
            first.write_bytes(b"".join([b"al", b"pha-1"]))
            second.write_bytes(b"".join([b"be", b"ta-2"]))
            proc = subprocess.run(
                ["sh", "-c", 'cmp "$1" "$2" >/dev/null', "sh", str(first), str(second)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stderr, "")
            missing = tmp_path / "missing"
            proc = subprocess.run(
                ["sh", "-c", 'cmp "$1" "$2" >/dev/null', "sh", str(first), str(missing)],
                capture_output=True,
                text=True,
            )
            self.assertGreater(proc.returncode, 1)
            self.assertNotEqual(proc.stderr, "")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_failed_firewall_restore_returns_failure_and_keeps_state(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
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
            dummy = venv_dir / "".join(["dum", "my"])
            dummy.write_bytes(b"".join([b"dummy-venv-", b"1"]))
            manifest = opos_home / "manifest"
            manifest.write_text(
                "launcher=" + str(launcher) + "\nvenv=" + str(venv_dir) + "\nrepo=" + str(home) + "\n",
                encoding="utf-8",
            )
            sentinel = b"inactive\n"
            state = opos_home / "".join(["fire", "wall.state"])
            state.write_bytes(sentinel)
            self.assertFalse((opos_home / "".join(["sshd_config", ".bak"])).exists())
            for name, code in (("ufw", "exit 1"), ("systemctl", "exit 0"), ("loginctl", "exit 1"), ("nft", "exit 1")):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\n" + code + "\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("".join(["fire", "wall=failed"]), proc.stdout)
            self.assertIn("".join(["retain", "ed"]), proc.stderr)
            self.assertIn(str(opos_home), proc.stderr)
            self.assertEqual(state.read_bytes(), sentinel)
            self.assertFalse(launcher.exists())
            self.assertFalse(manifest.exists())
            self.assertTrue(venv_dir.exists(), "venv must be retained when restore fails")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_ufw_disable_and_reload_failures_preserve_state_independently(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        state_name = "".join(["fire", "wall.state"])
        failed_mark = "".join(["fire", "wall=failed"])
        for name, disable_exit, reload_exit in (
            ("disable_fails", 1, 0),
            ("reload_fails", 0, 1),
        ):
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
                    sentinel = b"inactive\n"
                    state = opos_home / state_name
                    state.write_bytes(sentinel)
                    log = tmp_path / "".join(["ufw", ".log"])
                    log.write_text("", encoding="utf-8")
                    stub = fakebin / "ufw"
                    stub.write_text(
                        "#!/bin/sh\n"
                        + 'printf \'%s\\n\' "ufw $*" >> "'
                        + str(log)
                        + '"\n'
                        + 'case "$*" in *reload*) exit '
                        + str(reload_exit)
                        + ";; esac\n"
                        + 'case "$*" in *disable*) exit '
                        + str(disable_exit)
                        + ";; esac\n"
                        + "exit 0\n",
                        encoding="utf-8",
                    )
                    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    for tool in ("systemctl", "loginctl", "nft"):
                        other = fakebin / tool
                        other.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                        other.chmod(other.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos_home)
                    env["OPOS_BIN_DIR"] = str(bindir)
                    env["OPOS_ROOT"] = str(root)
                    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                    if not env.get("USER"):
                        env["USER"] = "opos-test-user"
                    proc = subprocess.run(
                        ["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60
                    )
                    self.assertNotEqual(proc.returncode, 0, name + proc.stdout + proc.stderr)
                    self.assertIn(failed_mark, proc.stdout, name)
                    self.assertEqual(state.read_bytes(), sentinel, name)
                    calls = log.read_text(encoding="utf-8")
                    self.assertIn("disable", calls, name)
                    self.assertIn("reload", calls, name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_backup_restore_requires_trusted_valid_backup(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
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
        self.assertIn('sshd -t -f "$OPOS_HOME/sshd_config.bak"', block)
        self.assertIn('[ ! -f "/etc/ssh/sshd_config" ]', block)

        def run_block(home, dest, fakebin, extra_env):
            replaced = block.replace("/etc/ssh/sshd_config", str(dest))
            dropin = pathlib.Path(home).parent / "no-dropin"
            script = '_sshd="skipped"\n'
            script += '_sshd_dropin="' + str(dropin) + '"\n'
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = pathlib.Path(home).parent / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            env.update(extra_env)
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return proc.stdout

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        cases = [
            ("non-root", "1000 600", "exit 0", False, False, False),
            ("group-write", "0 660", "exit 0", False, False, False),
            ("other-write", "0 602", "exit 0", False, False, False),
            ("symlink", "0 600", "exit 0", False, True, False),
            ("metadata-failure", None, "exit 0", False, False, False),
            ("missing-validator", "0 600", None, True, False, False),
            ("invalid-config", "0 600", "exit 1", False, False, False),
            ("copy-failure", "0 600", "exit 0", False, False, True),
            ("valid", "0 600", "exit 0", False, False, False),
        ]
        for name, meta, sshd_body, missing_sshd, as_link, fail_cp in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    home.mkdir()
                    fakebin = tmp_path / "fakebin"
                    fakebin.mkdir()
                    dest = tmp_path / "dest"
                    dest_b = ("".join(["de", "st"]) + "".join(["-se", "nt-"]) + "1").encode()
                    dest.write_bytes(dest_b)
                    bak = home / "sshd_config.bak"
                    bak_b = ("".join(["ba", "ck"]) + "".join(["-up", "-9"])).encode()
                    if as_link:
                        real = tmp_path / "real"
                        real.write_bytes(bak_b)
                        os.symlink(str(real), str(bak))
                    else:
                        bak.write_bytes(bak_b)
                    log = tmp_path / "sshd.log"
                    log.write_text("", encoding="utf-8")
                    if meta is None:
                        make_stub(fakebin / "stat", "exit 1")
                    else:
                        make_stub(fakebin / "stat", "printf '%s\\n' \"" + meta + '"')
                    if sshd_body is not None:
                        body = 'printf \'%s\\n\' "$@" >> "' + str(log) + '"\n' + sshd_body
                        make_stub(fakebin / "sshd", body)
                    if fail_cp:
                        make_stub(
                            fakebin / "cp",
                            'for a in "$@"; do last="$a"; done\n'
                            + "printf '%s' \""
                            + "".join(["par", "tial-", "9"])
                            + '" > "$last"\n'
                            + "exit 1",
                        )
                    status = run_block(home, dest, fakebin, {"SSHD_LOG": str(log)})
                    if name == "valid":
                        self.assertEqual(status, "succeeded")
                        self.assertEqual(dest.read_bytes(), bak_b)
                        self.assertFalse(bak.exists() or bak.is_symlink())
                        logged = log.read_text(encoding="utf-8")
                        self.assertIn("-t", logged.split())
                        self.assertIn("-f", logged.split())
                        self.assertIn(str(bak), logged)
                    else:
                        self.assertEqual(status, "failed")
                        self.assertEqual(dest.read_bytes(), dest_b)
                        if name == "invalid-config":
                            logged = log.read_text(encoding="utf-8")
                            self.assertIn("-t", logged.split())
                            self.assertIn("-f", logged.split())
                            self.assertIn(str(bak), logged)
                        if name in (
                            "non-root",
                            "group-write",
                            "other-write",
                            "symlink",
                            "metadata-failure",
                            "copy-failure",
                        ):
                            self.assertTrue(bak.exists() or bak.is_symlink())
                        if name == "copy-failure":
                            self.assertEqual(bak.read_bytes(), bak_b)
                            self.assertNotIn("".join(["par", "tial-", "9"]), dest.read_text(encoding="utf-8"))

    def test_installer_checks_have_no_unrelated_probe_assignments(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestInstallReview")
        targets = ("test_fallback_restores_sshd_firewall_and_service", "test_restore_steps_report_summary")
        actual = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(set(targets), actual & set(targets), "both installer methods must exist for hygiene check")
        stored = set()
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        stored.add(sub.id)
        for module in (review, *review._install_review_modules()):
            source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if (
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Store)
                    and node.id in ("key_name", "octet", "token_prefix", "private_ip", "home_path")
                ):
                    stored.add(node.id)
        forbidden = set(["token_prefix", "private_ip", "home_path", "key_name", "octet"])
        self.assertTrue(stored.isdisjoint(forbidden), stored & forbidden)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_fallback_restores_sshd_firewall_and_service(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_once(fail_ufw, fail_disable):
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
                write_stub(fakebin / "ufw", "exit " + ("1" if fail_ufw else "0") + "\n")
                disable_code = "exit 1\n" if fail_disable else "exit 0\n"
                write_stub(
                    fakebin / "systemctl",
                    "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                    + 'case "$*" in *disable*) '
                    + disable_code
                    + ";; esac\n"
                    + 'case "$*" in *daemon-reload*) exit 0;; esac\n'
                    + "exit 0\n",
                )
                for name in ("loginctl", "nft"):
                    write_stub(fakebin / name, "exit 1\n")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["OPOS_BIN_DIR"] = str(bindir)
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                return subprocess.run(
                    ["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60
                )

        proc = run_once(False, False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
        self.assertIn("".join(["fire", "wall=succeeded"]), line)
        self.assertIn("service=succeeded", line)
        proc = run_once(True, True)
        self.assertNotEqual(proc.returncode, 0)
        line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
        self.assertIn("".join(["fire", "wall=failed"]), line)
        self.assertIn("service=failed", line)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_fallback_restores_sshd_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            home.mkdir()
            opos_home.mkdir()
            dest = tmp_path / "dest"
            dest.write_bytes(b"".join([b"dest-bytes-", b"91"]))
            backup_bytes = b"".join([b"backup-bytes-", b"37"])
            bak = opos_home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(backup_bytes)
            dropin = tmp_path / "no-dropin"
            self.assertFalse(dropin.exists())
            unit = home / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_bytes(b"".join([b"unit-bytes-", b"52"]))
            state = opos_home / "".join(["fire", "wall.state"])
            state.write_bytes(b"inactive\n")
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            stat_stub = stubdir / "stat"
            stat_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"0 600\"\n", encoding="utf-8")
            stat_stub.chmod(stat_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            sshd_stub = stubdir / "sshd"
            sshd_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sshd_stub.chmod(sshd_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ufw_log = tmp_path / "ufw.log"
            ufw_log.write_text("", encoding="utf-8")
            ufw_stub = stubdir / "ufw"
            ufw_stub.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "'
                + str(ufw_log)
                + '"\n'
                + 'case "$*" in "--force disable") exit 0;; esac\n'
                + 'case "$*" in "reload") exit 0;; esac\n'
                + "exit 0\n",
                encoding="utf-8",
            )
            ufw_stub.chmod(ufw_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_log = tmp_path / "systemctl.log"
            ctl_log.write_text("", encoding="utf-8")
            ctl_stub = stubdir / "systemctl"
            ctl_stub.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "'
                + str(ctl_log)
                + '"\n'
                + 'case "$*" in *list-unit-files*--no-legend*opos-server.service*)\n'
                + "printf 'opos-server.service enabled\\n'\nexit 0;;\n"
                + "esac\n"
                + 'case "$*" in *disable*--now*opos-server.service*) exit 0;; esac\n'
                + 'case "$*" in *daemon-reload*) exit 0;; esac\n'
                + "exit 0\n",
                encoding="utf-8",
            )
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            replaced = _prepare_fallback_restore_snippet(dropin, dest)
            self.assertIn('_sshd_dropin="' + str(dropin) + '"', replaced)
            self.assertNotIn(str(dest) + ".d/60-opos.conf", replaced)
            preamble = (
                "is_hardening_active() { return 1; }\n"
                + "canon_repo() { printf '%s' \"$1\"; }\n"
                + "print_hardening_instructions() { :; }\n"
                + "opos_owns_linger() { return 1; }\n"
                + "is_linger_enabled() { return 1; }\n"
            )
            runner = tmp_path / "run.sh"
            runner.write_text(preamble + replaced, encoding="utf-8")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["DRY"] = "0"
            env["UVENV"] = ""
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
            self.assertIn("sshd=succeeded", line)
            self.assertIn("".join(["fire", "wall=succeeded"]), line)
            self.assertIn("service=succeeded", line)
            self.assertEqual(dest.read_bytes(), backup_bytes)
            self.assertFalse(bak.exists() or bak.is_symlink())
            self.assertEqual(ufw_log.read_text(encoding="utf-8").splitlines(), ["--force disable", "reload"])
            calls = ctl_log.read_text(encoding="utf-8")
            self.assertIn("--user disable --now opos-server.service", calls)
            self.assertIn("--user daemon-reload", calls)
            self.assertFalse(unit.exists())
            self.assertFalse(state.exists())
            self.assertFalse(dropin.exists())

    def test_rewritten_restore_snippet_rejects_backup_write(self):
        text = _read_install_sh()
        anchor = text.index("do_uninstall() {")
        idx = text.index("FOUND=0", anchor)
        leaf = "sshd_config" + ".bak"
        insert = '\nprintf x > "$OPOS_HOME/' + leaf + '"'
        token = "FOUND=0"
        mutant = text[: idx + len(token)] + insert + text[idx + len(token) :]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            dropin = tmp_path / "dropin"
            dest = tmp_path / "dest"
            with unittest.mock.patch.object(review, "_read_install_sh", return_value=mutant):
                with self.assertRaisesRegex(AssertionError, "backup write"):
                    _prepare_fallback_restore_snippet(dropin, dest)
            result = _prepare_fallback_restore_snippet(dropin, dest)
            self.assertTrue(result)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_restore_steps_report_summary(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_once(fail_ufw):
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
                unit.write_bytes(b"".join([b"unit-bytes-", b"4"]))
                write_stub(fakebin / "ufw", "exit " + ("1" if fail_ufw else "0") + "\n")
                write_stub(
                    fakebin / "systemctl",
                    "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                    + 'case "$*" in *disable*) exit 0;; esac\n'
                    + 'case "$*" in *daemon-reload*) exit 0;; esac\n'
                    + "exit 0\n",
                )
                for name in ("loginctl", "nft"):
                    write_stub(fakebin / name, "exit 1\n")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["OPOS_BIN_DIR"] = str(bindir)
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                return subprocess.run(
                    ["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env, timeout=60
                )

        good = run_once(False)
        good_line = next((x for x in good.stdout.splitlines() if "restore summary" in x), "")
        self.assertNotEqual(good_line, "", "success path must emit a restore summary")
        self.assertIn("".join(["fire", "wall=succeeded"]), good_line)
        bad = run_once(True)
        bad_line = next((x for x in bad.stdout.splitlines() if "restore summary" in x), "")
        self.assertNotEqual(bad_line, "", "failure path must emit a restore summary")
        self.assertIn("".join(["fire", "wall=failed"]), bad_line)
        self.assertNotEqual(good_line, bad_line, "summary must reflect the restoration outcome")
        self.assertNotEqual(bad.returncode, 0)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_restore_summary_reports_mixed_outcomes(self):
        text = _read_install_sh()
        start = text.index('    _sshd="skipped"')
        end = text.index("\n  fi\n  opos_owned_path", start)
        body = text[start:end]
        prod = "/etc/ssh/" + "sshd_config"
        prod_dropin = prod + ".d/60-opos.conf"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            state = tmp_path / "".join(["fire", "wall.state"])
            state.write_bytes(b"".join([b"state-bytes-", b"31"]))
            unit = tmp_path / ".config" / "systemd" / "user" / "opos-server.service"
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_bytes(b"".join([b"unit-bytes-", b"32"]))
            dest = tmp_path / "".join(["sshd", "_config"])
            dropin = tmp_path / "".join(["60-opos", ".conf"])
            dest.write_bytes(b"".join([b"dest-bytes-", b"33"]))
            stubs = tmp_path / "stubs"
            stubs.mkdir()
            for tool in ("cp", "mv", "rm", "mktemp", "stat", "sshd"):
                stub = stubs / tool
                stub.write_text(
                    "#!/bin/sh\n" + 'case "$*" in *"$OPOS_PROD_PREFIX"*) exit 1;; esac\nexec /bin/' + tool + ' "$@"\n',
                    encoding="utf-8",
                )
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            body = body.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
            script = "ufw() { return 1; }\n" + "systemctl() { return 0; }\n" + "id() { printf '1001\\n'; }\n" + body
            env = dict(os.environ)
            env.pop("SUDO_USER", None)
            env["OPOS_HOME"] = str(tmp_path)
            env["HOME"] = str(tmp_path)
            env["OPOS_PROD_PREFIX"] = prod
            env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn(
                "==> restore summary: sshd=skipped firewall=failed service=succeeded", proc.stdout.splitlines()
            )

    def test_restore_mixed_outcomes_isolates_host_ssh(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        start = src.index("def test_restore_summary_reports_mixed_outcomes")
        end = src.index("\n    def ", start)
        block = src[start:end]
        prod = "/etc/ssh/" + "sshd_config"
        prod_dropin = prod + ".d/60-opos.conf"
        self.assertIn(".replace(", block)
        self.assertNotIn(prod, block)
        self.assertNotIn(prod_dropin, block)
        for tool in ("cp", "mv", "rm", "mktemp", "stat", "sshd"):
            self.assertIn(tool, block)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_restore_reports_firewall_and_service_independently(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"

        def write_stub(path, body):
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_once(fail_ufw, fail_disable):
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
                unit.write_bytes(b"".join([b"unit-bytes-", b"7"]))
                write_stub(fakebin / "ufw", "exit " + ("1" if fail_ufw else "0") + "\n")
                disable_code = "exit 1\n" if fail_disable else "exit 0\n"
                write_stub(
                    fakebin / "systemctl",
                    "case \"$*\" in *list-unit-files*) printf 'opos-server.service disabled\\n'\nexit 0;; esac\n"
                    + 'case "$*" in *disable*) '
                    + disable_code
                    + ";; esac\n"
                    + 'case "$*" in *daemon-reload*) exit 0;; esac\n'
                    + "exit 0\n",
                )
                for name in ("loginctl", "nft"):
                    write_stub(fakebin / name, "exit 1\n")
                env = dict(os.environ)
                env["HOME"] = str(home)
                env["OPOS_HOME"] = str(opos_home)
                env["OPOS_BIN_DIR"] = str(bindir)
                env["OPOS_ROOT"] = str(root)
                env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
                if not env.get("USER"):
                    env["USER"] = "opos-test-user"
                return subprocess.run(["sh", str(install), "--uninstall"], capture_output=True, text=True, env=env)

        cases = [
            (False, True, "succeeded", "failed"),
            (True, False, "failed", "succeeded"),
        ]
        for fail_ufw, fail_disable, want_fw, want_svc in cases:
            with self.subTest(fail_ufw=fail_ufw, fail_disable=fail_disable):
                proc = run_once(fail_ufw, fail_disable)
                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
                self.assertNotEqual(line, "", "mixed outcome must emit a restore summary")
                self.assertIn("".join(["fire", "wall=" + want_fw]), line)
                self.assertIn("service=" + want_svc, line)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_restore_stages_before_atomic_replace(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
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
        self.assertIn('mktemp "/etc/ssh/sshd_config.opos.XXXXXX"', block)
        self.assertIn("cp -p", block)
        self.assertIn('sshd -t -f "$_sshd_tmp"', block)
        self.assertIn('mv -f "$_sshd_tmp"', block)
        self.assertIn('cmp "$OPOS_HOME/sshd_config.bak" "$_sshd_tmp"', block)

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_mode(mode):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / "".join(["sshd", "_config"])
            live_b = b"".join([b"li", b"ve-bytes-", b"11"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"22"])
            dest.write_bytes(live_b)
            bak = home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(bak_b)
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            if mode == "copy-fail":
                make_stub(
                    fakebin / "cp",
                    'printf \'cp %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + 'for a in "$@"; do last="$a"; done\n'
                    + "printf '%s' \""
                    + "".join(["par", "tial-", "9"])
                    + '" > "$last"\n'
                    + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "cp",
                    'printf \'cp %s\\n\' "$*" >> "' + str(ops) + '"\n' + 'exec /bin/cp "$@"',
                )
            if mode == "validation-fail":
                make_stub(
                    fakebin / "sshd",
                    'printf \'sshd %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "sshd",
                    'printf \'sshd %s\\n\' "$*" >> "' + str(ops) + '"\n' + "exit 0",
                )
            if mode == "rename-fail":
                make_stub(
                    fakebin / "mv",
                    'printf \'mv %s\\n\' "$*" >> "' + str(ops) + '"\n' + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "mv",
                    'printf \'mv %s\\n\' "$*" >> "' + str(ops) + '"\n' + 'exec /bin/mv "$@"',
                )
            replaced = block.replace("/etc/ssh/sshd_config", str(dest))
            dropin = tmp_path / "no-dropin"
            script = '_sshd="skipped"\n'
            script += '_sshd_dropin="' + str(dropin) + '"\n'
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            leftovers = sorted(p.name for p in sshdir.iterdir())
            ops_lines = ops.read_text(encoding="utf-8").splitlines()
            dest_b = dest.read_bytes() if dest.is_file() else None
            bak_b_now = bak.read_bytes() if bak.is_file() else None
            return tmp, proc.stdout, dest_b, bak_b_now, leftovers, ops_lines, live_b, bak_b

        for mode in ("copy-fail", "validation-fail", "rename-fail"):
            with self.subTest(mode=mode):
                tmp, status, dest_b, bak_now, leftovers, ops_lines, live_b, bak_b = run_mode(mode)
                try:
                    self.assertEqual(status, "failed")
                    self.assertEqual(dest_b, live_b)
                    self.assertEqual(bak_now, bak_b)
                    self.assertEqual(leftovers, ["".join(["sshd", "_config"])])
                    if mode == "validation-fail":
                        sshd_idxs = [i for i, l in enumerate(ops_lines) if l.startswith("sshd ")]
                        self.assertEqual(len(sshd_idxs), 2)
                        self.assertNotIn(".bak", ops_lines[sshd_idxs[1]])
                        self.assertEqual([l for l in ops_lines if l.startswith("mv ")], [])
                finally:
                    tmp.cleanup()
        with self.subTest(mode="success"):
            tmp, status, dest_b, bak_now, leftovers, ops_lines, live_b, bak_b = run_mode("success")
            try:
                self.assertEqual(status, "succeeded")
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertEqual(leftovers, ["".join(["sshd", "_config"])])
                sshd_idxs = [i for i, l in enumerate(ops_lines) if l.startswith("sshd ")]
                self.assertGreaterEqual(len(sshd_idxs), 2)
                staged_idx = sshd_idxs[-1]
                self.assertNotIn(".bak", ops_lines[staged_idx])
                mv_idx = next(i for i, l in enumerate(ops_lines) if l.startswith("mv "))
                self.assertLess(staged_idx, mv_idx)
            finally:
                tmp.cleanup()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_restore_stages_before_replacing_active_config(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
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
        dest_literal = "/etc/" + "ssh/" + "sshd_config"
        bak_leaf = "".join(["sshd_config", ".bak"])
        live_leaf = "".join(["sshd_config", ".live"])

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_case(cp_mode, sshd_reject_staged):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / "".join(["sshd", "_config"])
            live_b = b"".join([b"li", b"ve-bytes-", b"51"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"52"])
            dest.write_bytes(live_b)
            bak = home / bak_leaf
            bak.write_bytes(bak_b)
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            if cp_mode == "partial":
                make_stub(
                    fakebin / "cp",
                    'for a in "$@"; do last="$a"; done\n'
                    + 'src="$(eval echo \\${$(($# - 1))})"\n'
                    + 'head -c 2 "$src" > "$last"\n'
                    + "exit 1",
                )
            elif cp_mode == "mismatch":
                make_stub(
                    fakebin / "cp",
                    'for a in "$@"; do last="$a"; done\n'
                    + "printf '%s' \""
                    + "".join(["wro", "ng-bytes-", "9"])
                    + '" > "$last"\n'
                    + "exit 0",
                )
            else:
                make_stub(fakebin / "cp", 'exec /bin/cp "$@"')
            observed = tmp_path / "observed-live"
            if sshd_reject_staged:
                make_stub(
                    fakebin / "sshd",
                    'for a in "$@"; do last="$a"; done\n'
                    + 'case "$last" in *.opos.*) cat "'
                    + str(dest)
                    + '" > "'
                    + str(observed)
                    + '"\n'
                    + "exit 1;; esac\n"
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + "exit 0",
                )
            else:
                make_stub(
                    fakebin / "sshd",
                    'for a in "$@"; do last="$a"; done\n'
                    + 'case "$last" in *.opos.*) cat "'
                    + str(dest)
                    + '" > "'
                    + str(observed)
                    + '"\n'
                    + "exit 0;; esac\n"
                    + "exit 0",
                )
            replaced = block.replace(dest_literal, str(dest))
            dropin = tmp_path / "no-dropin"
            script = '_sshd="skipped"\n'
            script += '_sshd_dropin="' + str(dropin) + '"\n'
            script += replaced + "\n"
            script += "printf 'sshd=%s\\n' \"$_sshd\"\n"
            script += '[ "$_sshd" = failed ] && exit 1\n'
            script += "exit 0\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            dest_b = dest.read_bytes() if dest.is_file() else None
            bak_now = bak.read_bytes() if (bak.exists() or bak.is_symlink()) else None
            leftovers = sorted(p.name for p in sshdir.iterdir() if ".opos." in p.name)
            live_copy_exists = os.path.lexists(str(home / live_leaf))
            observed_b = observed.read_bytes() if observed.is_file() else None
            return tmp, proc, dest_b, bak_now, leftovers, live_copy_exists, live_b, bak_b, observed_b

        for name, cp_mode, sshd_reject in (
            ("partial_copy", "partial", False),
            ("mismatched_copy", "mismatch", False),
            ("sshd_rejects_staged", "ok", True),
        ):
            with self.subTest(name=name):
                tmp, proc, dest_b, bak_now, leftovers, live_copy_exists, live_b, bak_b, observed_b = run_case(
                    cp_mode, sshd_reject
                )
                try:
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn("sshd=failed", proc.stdout, name)
                    self.assertEqual(dest_b, live_b, name)
                    self.assertEqual(bak_now, bak_b, name)
                    self.assertEqual(leftovers, [], name)
                    self.assertFalse(live_copy_exists, name)
                    if name == "sshd_rejects_staged":
                        self.assertIsNotNone(observed_b, name)
                        self.assertEqual(observed_b, live_b, name)
                finally:
                    tmp.cleanup()
        with self.subTest(name="validation_succeeds"):
            tmp, proc, dest_b, bak_now, leftovers, live_copy_exists, live_b, bak_b, observed_b = run_case("ok", False)
            try:
                self.assertEqual(observed_b, live_b)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("sshd=succeeded", proc.stdout)
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertEqual(leftovers, [])
                self.assertFalse(live_copy_exists)
            finally:
                tmp.cleanup()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_restore_stages_validates_and_replaces_atomically(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
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
        self.assertIn('mktemp "/etc/ssh/sshd_config.opos.XXXXXX"', block)
        self.assertIn("cp -p", block)
        self.assertIn('sshd -t -f "$_sshd_tmp"', block)
        self.assertIn('mv -f "$_sshd_tmp"', block)
        self.assertIn('cmp "$OPOS_HOME/sshd_config.bak" "$_sshd_tmp"', block)

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_mode(mode):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / "".join(["sshd", "_config"])
            live_b = b"".join([b"li", b"ve-bytes-", b"71"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"72"])
            dest.write_bytes(live_b)
            bak = home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(bak_b)
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            if mode in ("copy-fail", "copy-incomplete-success"):
                make_stub(
                    fakebin / "cp",
                    'printf \'cp %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + 'for a in "$@"; do last="$a"; done\n'
                    + "printf '%s' \""
                    + "".join(["par", "tial-", "9"])
                    + '" > "$last"\n'
                    + ("exit 1" if mode == "copy-fail" else "exit 0"),
                )
            else:
                make_stub(
                    fakebin / "cp",
                    'printf \'cp %s\\n\' "$*" >> "' + str(ops) + '"\n' + 'exec /bin/cp "$@"',
                )
            if mode == "validation-fail":
                make_stub(
                    fakebin / "sshd",
                    'printf \'sshd %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "sshd",
                    'printf \'sshd %s\\n\' "$*" >> "' + str(ops) + '"\n' + "exit 0",
                )
            if mode == "rename-fail":
                make_stub(
                    fakebin / "mv",
                    'if [ ! -f "'
                    + str(bak)
                    + '" ]; then exit 99; fi\n'
                    + 'printf \'mv %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + "exit 1",
                )
            else:
                make_stub(
                    fakebin / "mv",
                    'if [ ! -f "'
                    + str(bak)
                    + '" ]; then exit 99; fi\n'
                    + 'printf \'mv %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + 'exec /bin/mv "$@"',
                )
            replaced = block.replace("/etc/ssh/" + "sshd_config", str(dest))
            dropin = tmp_path / "no-dropin"
            script = '_sshd="skipped"\n'
            script += '_sshd_dropin="' + str(dropin) + '"\n'
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ops_lines = ops.read_text(encoding="utf-8").splitlines()
            names = sorted(p.name for p in sshdir.iterdir())
            dest_b = dest.read_bytes() if dest.is_file() else None
            bak_now = bak.read_bytes() if (bak.exists() or bak.is_symlink()) else None
            return tmp, proc.stdout, dest_b, bak_now, names, ops_lines, live_b, bak_b

        for mode in ("copy-fail", "copy-incomplete-success", "validation-fail", "rename-fail"):
            with self.subTest(mode=mode):
                tmp, status, dest_b, bak_now, names, ops_lines, live_b, bak_b = run_mode(mode)
                try:
                    self.assertEqual(status, "failed")
                    self.assertEqual(dest_b, live_b)
                    self.assertEqual(bak_now, bak_b)
                    self.assertEqual(names, ["".join(["sshd", "_config"])])
                    if mode == "validation-fail":
                        sshd_idxs = [i for i, l in enumerate(ops_lines) if l.startswith("sshd ")]
                        self.assertEqual(len(sshd_idxs), 2)
                        self.assertNotIn(".bak", ops_lines[sshd_idxs[1]])
                        self.assertEqual([l for l in ops_lines if l.startswith("mv ")], [])
                    if mode == "copy-incomplete-success":
                        sshd_lines = [l for l in ops_lines if l.startswith("sshd ")]
                        self.assertEqual(len(sshd_lines), 1)
                        self.assertIn(".bak", sshd_lines[0])
                        self.assertEqual([l for l in ops_lines if l.startswith("mv ")], [])
                finally:
                    tmp.cleanup()
        with self.subTest(mode="success"):
            tmp, status, dest_b, bak_now, names, ops_lines, live_b, bak_b = run_mode("success")
            try:
                self.assertEqual(status, "succeeded")
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertEqual(names, ["".join(["sshd", "_config"])])
                sshd_idxs = [i for i, l in enumerate(ops_lines) if l.startswith("sshd ")]
                self.assertGreaterEqual(len(sshd_idxs), 2)
                staged_idx = sshd_idxs[-1]
                self.assertNotIn(".bak", ops_lines[staged_idx])
                mv_idx = next(i for i, l in enumerate(ops_lines) if l.startswith("mv "))
                self.assertLess(staged_idx, mv_idx)
            finally:
                tmp.cleanup()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_restore_preserves_live_copy_until_verified(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
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

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_mode(mode):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / "".join(["sshd", "_config"])
            live_b = b"".join([b"li", b"ve-bytes-", b"83"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"84"])
            dest.write_bytes(live_b)
            bak = home / "".join(["sshd_config", ".bak"])
            bak.write_bytes(bak_b)
            live_copy = home / "".join(["sshd_config", ".live"])
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            make_stub(
                fakebin / "cp",
                'printf \'cp %s\\n\' "$*" >> "' + str(ops) + '"\n' + 'exec /bin/cp "$@"',
            )
            make_stub(
                fakebin / "sshd",
                'printf \'sshd %s\\n\' "$*" >> "' + str(ops) + '"\n' + "exit 0",
            )
            if mode == "success":
                make_stub(
                    fakebin / "mv",
                    'cmp -s "'
                    + str(live_copy)
                    + '" "'
                    + str(dest)
                    + '" || exit 99\n'
                    + 'printf \'mv %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + 'exec /bin/mv "$@"',
                )
            elif mode == "verify-fail":
                make_stub(
                    fakebin / "mv",
                    'printf \'mv %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + '/bin/mv "$@"\n'
                    + "printf 'corrupt' > \""
                    + str(dest)
                    + '"\n'
                    + "exit 0",
                )
            else:
                make_stub(
                    fakebin / "mv",
                    'printf \'mv %s\\n\' "$*" >> "' + str(ops) + '"\n' + "exit 1",
                )
            replaced = block.replace("/etc/ssh/" + "sshd_config", str(dest))
            dropin = tmp_path / "no-dropin"
            script = '_sshd="skipped"\n'
            script += '_sshd_dropin="' + str(dropin) + '"\n'
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            names = sorted(p.name for p in sshdir.iterdir())
            dest_b = dest.read_bytes() if dest.is_file() and not dest.is_symlink() else None
            bak_now = bak.read_bytes() if (bak.exists() or bak.is_symlink()) else None
            live_now = live_copy.read_bytes() if (live_copy.exists() or live_copy.is_symlink()) else None
            live_exists = os.path.lexists(str(live_copy))
            return (
                tmp,
                proc.stdout,
                proc.stderr,
                dest_b,
                bak_now,
                live_now,
                live_exists,
                names,
                live_b,
                bak_b,
                bak,
                dest,
                live_copy,
            )

        with self.subTest(mode="success"):
            tmp, status, err, dest_b, bak_now, live_now, live_exists, names, live_b, bak_b, bak, dest, live_copy = (
                run_mode("success")
            )
            try:
                self.assertEqual(status, "succeeded")
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertFalse(live_exists)
                self.assertEqual(names, ["".join(["sshd", "_config"])])
            finally:
                tmp.cleanup()
        with self.subTest(mode="verify-fail"):
            tmp, status, err, dest_b, bak_now, live_now, live_exists, names, live_b, bak_b, bak, dest, live_copy = (
                run_mode("verify-fail")
            )
            try:
                self.assertEqual(status, "failed")
                self.assertEqual(live_now, live_b)
                self.assertEqual(bak_now, bak_b)
                self.assertIn(str(live_copy), err)
                self.assertEqual(names, ["".join(["sshd", "_config"])])
            finally:
                tmp.cleanup()
        with self.subTest(mode="rename-fail"):
            tmp, status, err, dest_b, bak_now, live_now, live_exists, names, live_b, bak_b, bak, dest, live_copy = (
                run_mode("rename-fail")
            )
            try:
                self.assertEqual(status, "failed")
                self.assertEqual(dest_b, live_b)
                self.assertEqual(bak_now, bak_b)
                self.assertFalse(live_exists)
                self.assertEqual(names, ["".join(["sshd", "_config"])])
            finally:
                tmp.cleanup()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_restore_retry_preserves_original_live_copy(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
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
        dest_literal = "/etc/ssh/" + "sshd_config"
        bak_leaf = "".join(["sshd_config", ".bak"])
        live_leaf = "".join(["sshd_config", ".live"])
        staged_leaf = "".join(["sshd", "_config"])

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / staged_leaf
            live_b = b"".join([b"li", b"ve-bytes-", b"95"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"96"])
            dest.write_bytes(live_b)
            bak = home / bak_leaf
            bak.write_bytes(bak_b)
            live_copy = home / live_leaf
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            corrupt_b = b"".join([b"cor", b"rupt-9"])
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            make_stub(fakebin / "sshd", 'printf \'sshd %s\\n\' "$*" >> "' + str(ops) + '"\nexit 0')
            make_stub(fakebin / "cp", 'printf \'cp %s\\n\' "$*" >> "' + str(ops) + '"\nexec /bin/cp "$@"')
            make_stub(
                fakebin / "mv",
                'printf \'mv %s\\n\' "$*" >> "'
                + str(ops)
                + '"\n/bin/mv "$@"\nprintf \'%s\' "'
                + corrupt_b.decode()
                + '" > "'
                + str(dest)
                + '"\nexit 0',
            )
            replaced = block.replace(dest_literal, str(dest))
            dropin = tmp_path / "no-dropin"
            script = '_sshd="skipped"\n'
            script += '_sshd_dropin="' + str(dropin) + '"\n'
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            for idx in range(3):
                proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, "failed")
                self.assertEqual(live_copy.read_bytes(), live_b)
                self.assertEqual(bak.read_bytes(), bak_b)
                self.assertEqual(dest.read_bytes(), corrupt_b)
                if idx > 0:
                    self.assertIn(str(live_copy), proc.stderr)
            mv_lines = [line for line in ops.read_text(encoding="utf-8").splitlines() if line.startswith("mv ")]
            self.assertEqual(len(mv_lines), 1)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_restore_rejects_directory_and_verifies_install(self):
        text = _read_install_sh()
        marker = 'if [ -f "$OPOS_HOME/sshd_config.bak" ]; then'
        start = text.index(marker)
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
        dest_literal = "/etc/ssh/" + "sshd_config"
        bak_leaf = "".join(["sshd_config", ".bak"])
        staged_leaf = "".join(["sshd", "_config"])

        def make_stub(path, body):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        def run_mode(mode):
            tmp = tempfile.TemporaryDirectory()
            tmp_path = pathlib.Path(tmp.name)
            home = tmp_path / "home"
            home.mkdir()
            sshdir = tmp_path / "ssh"
            sshdir.mkdir()
            dest = sshdir / staged_leaf
            live_b = b"".join([b"li", b"ve-bytes-", b"31"])
            bak_b = b"".join([b"ba", b"ckup-bytes-", b"52"])
            dest.write_bytes(live_b)
            bak = home / bak_leaf
            bak.write_bytes(bak_b)
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            ops = tmp_path / "ops.log"
            ops.write_text("", encoding="utf-8")
            target_dir = tmp_path / "targetdir"
            make_stub(fakebin / "stat", "printf '%s\\n' \"0 600\"")
            make_stub(
                fakebin / "cp",
                'printf \'cp %s\\n\' "$*" >> "' + str(ops) + '"\n' + 'exec /bin/cp "$@"',
            )
            if mode == "dir":
                make_stub(
                    fakebin / "sshd",
                    'printf \'sshd %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + 'rm -rf "'
                    + str(dest)
                    + '"\n'
                    + 'mkdir -p "'
                    + str(dest)
                    + '"\n'
                    + "exit 0",
                )
            elif mode == "symlink-dir":
                make_stub(
                    fakebin / "sshd",
                    'printf \'sshd %s\\n\' "$*" >> "'
                    + str(ops)
                    + '"\n'
                    + 'case "$*" in *.bak*) exit 0;; esac\n'
                    + 'mkdir -p "'
                    + str(target_dir)
                    + '"\n'
                    + 'rm -rf "'
                    + str(dest)
                    + '"\n'
                    + 'ln -s "'
                    + str(target_dir)
                    + '" "'
                    + str(dest)
                    + '"\n'
                    + "exit 0",
                )
            else:
                make_stub(
                    fakebin / "sshd",
                    'printf \'sshd %s\\n\' "$*" >> "' + str(ops) + '"\n' + "exit 0",
                )
            if mode == "false-success-mv":
                make_stub(
                    fakebin / "mv",
                    'printf \'mv %s\\n\' "$*" >> "' + str(ops) + '"\n' + "exit 0",
                )
            else:
                make_stub(
                    fakebin / "mv",
                    'printf \'mv %s\\n\' "$*" >> "' + str(ops) + '"\n' + 'exec /bin/mv "$@"',
                )
            replaced = block.replace(dest_literal, str(dest))
            dropin = tmp_path / "no-dropin"
            script = '_sshd="skipped"\n'
            script += '_sshd_dropin="' + str(dropin) + '"\n'
            script += replaced + "\n"
            script += "printf '%s' \"$_sshd\"\n"
            runner = tmp_path / "run.sh"
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["PATH"] = str(fakebin) + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
            proc = subprocess.run(["sh", str(runner)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ops_lines = ops.read_text(encoding="utf-8").splitlines()
            mv_lines = [line for line in ops_lines if line.startswith("mv ")]
            names = sorted(p.name for p in sshdir.iterdir())
            bak_now = bak.read_bytes() if (bak.exists() or bak.is_symlink()) else None
            if dest.is_symlink():
                dest_kind = "symlink"
                dest_b = None
            elif dest.is_dir():
                dest_kind = "dir"
                dest_b = None
            elif dest.is_file():
                dest_kind = "file"
                dest_b = dest.read_bytes()
            else:
                dest_kind = "missing"
                dest_b = None
            return tmp, proc.stdout, dest_kind, dest_b, bak_now, names, mv_lines, live_b, bak_b

        for mode in ("dir", "symlink-dir"):
            with self.subTest(mode=mode):
                tmp, status, kind, dest_b, bak_now, names, mv_lines, live_b, bak_b = run_mode(mode)
                try:
                    self.assertEqual(status, "failed")
                    self.assertEqual(bak_now, bak_b)
                    self.assertEqual(mv_lines, [], "no move may run after destination became a directory")
                    self.assertEqual(names, [staged_leaf], "staged file must be removed, destination entry retained")
                    if mode == "dir":
                        self.assertEqual(kind, "dir")
                    else:
                        self.assertEqual(kind, "symlink")
                finally:
                    tmp.cleanup()
        with self.subTest(mode="false-success-mv"):
            tmp, status, kind, dest_b, bak_now, names, mv_lines, live_b, bak_b = run_mode("false-success-mv")
            try:
                self.assertEqual(status, "failed")
                self.assertEqual(kind, "file")
                self.assertEqual(dest_b, live_b)
                self.assertEqual(bak_now, bak_b)
                self.assertEqual(names, [staged_leaf])
            finally:
                tmp.cleanup()
        with self.subTest(mode="success"):
            tmp, status, kind, dest_b, bak_now, names, mv_lines, live_b, bak_b = run_mode("success")
            try:
                self.assertEqual(status, "succeeded")
                self.assertEqual(kind, "file")
                self.assertEqual(dest_b, bak_b)
                self.assertIsNone(bak_now)
                self.assertEqual(names, [staged_leaf])
                self.assertTrue(mv_lines)
            finally:
                tmp.cleanup()

    def test_uninstall_subprocesses_are_bounded(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestInstallReview")
        wanted = ("test_fallback_restores_sshd_firewall_and_service", "test_restore_steps_report_summary")
        found = 0
        for node in cls.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        is_run = (
                            isinstance(func, ast.Attribute)
                            and func.attr == "run"
                            and isinstance(func.value, ast.Name)
                            and func.value.id == "subprocess"
                        )
                        if is_run and any(
                            isinstance(a, ast.List)
                            and any(isinstance(e, ast.Constant) and e.value == "--uninstall" for e in a.elts)
                            for a in sub.args
                        ):
                            keys = [k.arg for k in sub.keywords]
                            self.assertIn("timeout", keys, node.name + " uninstall run must bound execution")
                            found += 1
        self.assertEqual(found, 2, "expected two bounded uninstall runs")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_dropin_removal_keeps_backup_when_restore_fails(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc, bak, bak_bytes, dest, dropin = _run_sshd_dropin_restore(pathlib.Path(tmp), 1)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
            self.assertIn("sshd=failed", line)
            self.assertEqual(bak.read_bytes(), bak_bytes)
            self.assertEqual(dest.read_bytes(), b"dest-" + b"bytes-61")
            self.assertFalse(dropin.exists())

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_dropin_removal_restores_config_before_deleting_backup(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proc, bak, bak_bytes, dest, dropin = _run_sshd_dropin_restore(pathlib.Path(tmp), 0)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "restore summary" in x), "")
            self.assertIn("sshd=succeeded", line)
            self.assertEqual(dest.read_bytes(), bak_bytes)
            self.assertFalse(bak.exists() or bak.is_symlink())
            self.assertFalse(dropin.exists())

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_sshd_verifies_recorded_content_and_preserves_admin_edits(self):
        text = _read_install_sh()
        start = text.index('    _sshd="skipped"')
        end = text.index("\n  fi\n  opos_owned_path", start)
        body = text[start:end]
        prod = "/etc/ssh/" + "sshd_config"
        prod_dropin = prod + ".d/60-opos.conf"
        baseline = "# Managed by OPOS - sshd drop-in\n" + "X\n"
        admin_line = "".join(["admin-", "edit\n"])
        cases = [
            ("verified-unmodified", True, False),
            ("modified", True, True),
            ("missing-records-unmodified", False, False),
            ("missing-records-modified", False, True),
        ]
        for name, has_record, modified in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    home = tmp_path / "home"
                    opos = tmp_path / "opos"
                    stubs = tmp_path / "stubs"
                    home.mkdir()
                    opos.mkdir()
                    stubs.mkdir()
                    dest = tmp_path / "dest"
                    dest.write_bytes(b"dest-" + b"bytes-61")
                    bak_bytes = b"bak-" + b"bytes-62"
                    bak = opos / ("sshd_config" + ".bak")
                    bak.write_bytes(bak_bytes)
                    dropin = tmp_path / "dropin"
                    content = baseline + admin_line if modified else baseline
                    dropin.write_text(content, encoding="utf-8")
                    if has_record:
                        (opos / "sshd_dropin.expected").write_text(baseline, encoding="utf-8")
                    self.assertFalse((opos / "sshd_dropin").exists(), name)
                    if not has_record:
                        self.assertFalse((opos / "sshd_dropin.expected").exists(), name)
                    stat_path = stubs / "stat"
                    stat_path.write_text("#!/bin/sh\n" + "printf '0 600'\n", encoding="utf-8")
                    stat_path.chmod(0o755)
                    sshd_path = stubs / "sshd"
                    sshd_path.write_text("#!/bin/sh\n" + "exit 0\n", encoding="utf-8")
                    sshd_path.chmod(0o755)
                    systemctl_path = stubs / "systemctl"
                    systemctl_path.write_text("#!/bin/sh\n" + "exit 0\n", encoding="utf-8")
                    systemctl_path.chmod(0o755)
                    self.assertTrue(dropin.is_file(), "drop-in must exist before uninstall snippet runs")
                    dropin_before = dropin.read_bytes()
                    script = body.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
                    env = dict(os.environ)
                    env["HOME"] = str(home)
                    env["OPOS_HOME"] = str(opos)
                    env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
                    proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    if name == "verified-unmodified":
                        self.assertFalse(dropin.exists(), name)
                        self.assertIn("sshd=succeeded", proc.stdout, name)
                        self.assertEqual(dest.read_bytes(), bak_bytes, name)
                        self.assertFalse(bak.exists() or bak.is_symlink(), name)
                    else:
                        self.assertTrue(dropin.is_file(), name)
                        self.assertEqual(dropin.read_bytes(), dropin_before, name)
                        self.assertIn("sshd=failed", proc.stdout, name)
                        self.assertEqual(bak.read_bytes(), bak_bytes, name)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_preserved_snapshot_does_not_overwrite_existing(self):
        text = _read_install_sh()
        start = text.index('    _sshd="skipped"')
        end = text.index("\n  fi\n  opos_owned_path", start)
        body = text[start:end]
        prod = "/etc/ssh/" + "sshd_config"
        prod_dropin = prod + ".d/60-opos.conf"
        baseline = "# Managed by OPOS - sshd drop-in\n" + "X\n"
        admin_line = "".join(["admin-", "edit\n"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos = tmp_path / "opos"
            stubs = tmp_path / "stubs"
            home.mkdir()
            opos.mkdir()
            stubs.mkdir()
            dest = tmp_path / "dest"
            dest.write_bytes(b"dest-" + b"bytes-61")
            bak_bytes = b"bak-" + b"bytes-62"
            bak = opos / ("sshd_config" + ".bak")
            bak.write_bytes(bak_bytes)
            dropin = tmp_path / "dropin"
            dropin.write_text(baseline + admin_line, encoding="utf-8")
            (opos / "sshd_dropin.expected").write_text(baseline, encoding="utf-8")
            snapshot = opos / ("sshd_dropin" + ".preserved")
            snapshot_bytes = b"snapshot-" + b"bytes-63"
            snapshot.write_bytes(snapshot_bytes)
            stat_path = stubs / "stat"
            stat_path.write_text("#!/bin/sh\n" + "printf '0 600'\n", encoding="utf-8")
            stat_path.chmod(0o755)
            sshd_path = stubs / "sshd"
            sshd_path.write_text("#!/bin/sh\n" + "exit 0\n", encoding="utf-8")
            sshd_path.chmod(0o755)
            systemctl_path = stubs / "systemctl"
            systemctl_path.write_text("#!/bin/sh\n" + "exit 0\n", encoding="utf-8")
            systemctl_path.chmod(0o755)
            modified_before = dropin.read_bytes()
            script = body.replace(prod_dropin, str(dropin)).replace(prod, str(dest))
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos)
            env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh"], input=script, capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("sshd=failed", proc.stdout)
            self.assertTrue(snapshot.is_file())
            self.assertEqual(snapshot.read_bytes(), snapshot_bytes)
            self.assertTrue(dropin.is_file())
            self.assertEqual(dropin.read_bytes(), modified_before)
            self.assertEqual(bak.read_bytes(), bak_bytes)

    def test_backup_probe_exception_is_exact(self):
        _assert_snippet_confined(_EXPECTED_BACKUP_PROBE + "\n")
        _assert_snippet_confined("  " + _EXPECTED_BACKUP_PROBE + "  \n")
        leaf = "".join(["sshd_config", ".bak"])
        home = "".join(["$OPOS_", "HOME"])
        prefix = _EXPECTED_BACKUP_PROBE
        redir = prefix + " > " + '"$OPOS_HOME/"' + leaf
        with self.assertRaisesRegex(AssertionError, "backup write"):
            _assert_snippet_confined(redir + "\n")
        q = '"' + home + "/" + leaf + '"'
        writes = [prefix + "; cp a " + q, prefix + " && printf x > " + q, prefix + " | tee " + q]
        for item in writes:
            with self.subTest(item=item):
                with self.assertRaisesRegex(AssertionError, "backup write"):
                    _assert_snippet_confined(item + "\n")
        reads = [prefix + " && cp -p " + q + ' "$T"', prefix.replace("test -f", "test -d")]
        for item in reads:
            with self.subTest(item=item):
                _assert_snippet_confined(item + "\n")
        star = chr(42)
        ordinary = "rm -rf " + '"$BASE"/' + star
        with self.assertRaises(AssertionError):
            _assert_snippet_confined(ordinary + "\n")

    def test_writes_backup_detects_backup_writes(self):
        leaf = "sshd_config" + ".bak"
        bak = '"$OPOS_HOME/' + leaf + '"'
        bad = [
            "printf x > " + bak,
            "printf x >>" + bak,
            "cp evil " + bak,
            "echo x | tee " + bak,
            "true; ln -s /x " + bak,
        ]
        good = [
            "cp -p " + bak + ' "$_sshd_tmp"',
            "sshd -t -f " + bak,
            "rm -f " + bak,
            "cmp " + bak + ' "$_sshd_tmp" >/dev/null',
            "if [ -f " + bak + " ]; then",
            "echo ok # > " + bak,
            _EXPECTED_BACKUP_PROBE,
        ]
        for line in bad:
            with self.subTest(line=line):
                self.assertTrue(_writes_backup(line))
        for line in good:
            with self.subTest(line=line):
                self.assertFalse(_writes_backup(line))

    def test_writes_backup_detects_inline_control_bodies(self):
        leaf = "sshd_config" + ".bak"
        bak = '"$OPOS_HOME/' + leaf + '"'
        bad = [
            "if true; then cp evil BAK; fi",
            "if false; then :; else cp evil BAK; fi",
            "for item in x; do cp evil BAK; done",
            "while false; do cp evil BAK; done",
        ]
        for line in bad:
            with self.subTest(line=line):
                snippet = line.replace("BAK", bak)
                self.assertTrue(_writes_backup(snippet))
                with self.assertRaisesRegex(AssertionError, "backup write"):
                    _assert_snippet_confined(snippet)
        good = ["if [ -f BAK ]; then :; fi", 'if true; then cp -p BAK "$_sshd_tmp"; fi']
        for line in good:
            with self.subTest(line=line):
                self.assertFalse(_writes_backup(line.replace("BAK", bak)))


if __name__ == "__main__":
    unittest.main()
