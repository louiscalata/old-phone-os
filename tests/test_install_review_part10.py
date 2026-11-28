# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import unittest

# Direct scripts under -P and -I need the checkout root before importing shared helpers.
if not __package__:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.install_review_support import (
    read_install_sh,
    run_retain_rollback,
)


class TestInstallReview(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_rollback_publication_rejects_concurrent_destination(self):
        text = read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = (
            _extract("opos_owned_path")
            + _extract("canon_file")
            + _extract("py_ok")
            + _extract("find_python")
            + _extract("retain_hardening_rollback")
        )
        leaf = "".join(["opos-harden-", "rollback.sh"])
        helper_rel = "".join(["harden-device", ".sh"])
        stage_pat = "".join([".opos-harden-rollback."])
        ok_mark = "".join(["hardening rollback retained at "])
        for mode in ("directory", "symlink"):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    clone = tmp_path / "clone"
                    deploy = clone / "deploy"
                    deploy.mkdir(parents=True)
                    helper_src = deploy / helper_rel
                    helper_bytes = b"".join([b"helper-", b"bytes-7"])
                    helper_src.write_bytes(helper_bytes)
                    home = tmp_path / "home"
                    home.mkdir()
                    dest = home / leaf
                    target = tmp_path / "".join(["tar", "getdir"])
                    target.mkdir()
                    marker = target / "".join(["keep", ".txt"])
                    marker_bytes = b"".join([b"keep-", b"bytes-3"])
                    marker.write_bytes(marker_bytes)
                    runner = tmp_path / "run.sh"
                    if mode == "directory":
                        plant = 'mkdir -p "$OPOS_PLANT"\n'
                    else:
                        plant = 'mkdir -p "$OPOS_TARGET"\nln -s "$OPOS_TARGET" "$OPOS_PLANT"\n'
                    script = (
                        helpers + 'chmod() {\ncommand chmod "$@"\n' + plant + "}\n" + 'retain_hardening_rollback "$1"\n'
                    )
                    runner.write_text(script, encoding="utf-8")
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(home)
                    env["OPOS_PLANT"] = str(dest)
                    env["OPOS_TARGET"] = str(target)
                    env["DRY"] = "0"
                    proc = subprocess.run(
                        ["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30
                    )
                    self.assertNotEqual(proc.returncode, 0, mode)
                    self.assertIn(mode, ("directory", "symlink"))
                    if mode == "directory":
                        self.assertTrue(dest.is_dir() and not dest.is_symlink(), mode)
                    else:
                        self.assertTrue(dest.is_symlink(), mode)
                        self.assertEqual(os.readlink(str(dest)), str(target), mode)
                    self.assertEqual(marker.read_bytes(), marker_bytes, mode)
                    nested = dest / leaf
                    self.assertFalse(os.path.lexists(str(nested)), mode)
                    leftovers = [p for p in home.iterdir() if stage_pat in p.name]
                    self.assertEqual(leftovers, [], mode)
                    self.assertNotIn(ok_mark, proc.stdout, mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-8"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(dest.is_file() and not dest.is_symlink())
            self.assertEqual(dest.read_bytes(), helper_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(dest)).st_mode), 0o700)
            leftovers = [p for p in home.iterdir() if stage_pat in p.name]
            self.assertEqual(leftovers, [])
            self.assertIn(ok_mark, proc.stdout)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_rollback_publication_rejects_destination_replaced_after_staging(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp_path = pathlib.Path(td)
            victim = tmp_path / "victim"
            victim.write_bytes(b"".join([b"vic", b"tim-bytes-5"]))
            preamble = 'cp() { command cp "$@" || return 1; ln -s "$OPOS_VICTIM" "$OPOS_DEST"; }\n'
            proc, home, dest, helper_bytes = run_retain_rollback(tmp_path, preamble)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(victim.read_bytes(), b"".join([b"vic", b"tim-bytes-5"]))
            self.assertTrue(dest.is_symlink())
            self.assertEqual(os.readlink(str(dest)), str(victim))
            self.assertFalse(any(".opos-harden-rollback." in p.name for p in home.iterdir()))
            self.assertNotIn("hardening rollback retained at ", proc.stdout)

    @unittest.skipIf(sys.platform == "win32", "preexec_fn is POSIX-only")
    def test_cleanup_preserves_exit_and_signal_status(self):
        text = read_install_sh()
        start = text.index("cleanup() {")
        end = text.index('if [ "$UNINST"', start)
        fragment = text[start:end]
        cases = [
            ("exit_zero", "exit 0\n", 0, False),
            ("exit_seven", "exit 7\n", 7, False),
            ("sigint", 'true\nkill -INT "$$"\n: > "$CONTINUATION"\n', 130, True),
            ("sigterm", 'true\nkill -TERM "$$"\n: > "$CONTINUATION"\n', 143, True),
        ]
        for name, ending, want_rc, is_signal in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    opos_home = tmp_path / "opos"
                    opos_home.mkdir()
                    staging = pathlib.Path(tempfile.mkdtemp(prefix=".venv-staging.", dir=str(opos_home)))
                    sentinel = staging / "sentinel"
                    sentinel.write_text("sentinel\n", encoding="utf-8")
                    tmp_launcher = tmp_path / "launcher.tmp"
                    tmp_launcher.write_text("launcher\n", encoding="utf-8")
                    tmp_manifest = tmp_path / "manifest.tmp"
                    tmp_manifest.write_text("manifest\n", encoding="utf-8")
                    continuation = tmp_path / "continued"
                    runner = tmp_path / "run.sh"
                    runner.write_text(
                        fragment + '_staging_root="$STAGING_ROOT"\n' + ending,
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(opos_home)
                    env["STAGING_ROOT"] = str(staging)
                    env["TMP_LAUNCHER"] = str(tmp_launcher)
                    env["TMP_MANIFEST"] = str(tmp_manifest)
                    env["CONTINUATION"] = str(continuation)

                    def _reset_signals():
                        signal.signal(signal.SIGINT, signal.SIG_DFL)
                        signal.signal(signal.SIGTERM, signal.SIG_DFL)

                    proc = subprocess.run(
                        ["sh", str(runner)],
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=30,
                        preexec_fn=_reset_signals,
                    )
                    self.assertEqual(proc.returncode, want_rc, proc.stdout + proc.stderr)
                    self.assertFalse(staging.exists(), "staging root must be removed")
                    self.assertFalse(sentinel.exists(), "staging sentinel must be removed")
                    self.assertFalse(tmp_launcher.exists(), "temporary launcher must be removed")
                    self.assertFalse(tmp_manifest.exists(), "temporary manifest must be removed")
                    if is_signal:
                        self.assertFalse(continuation.exists(), "signal must stop execution")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_rollback_retention_partial_copy_failure_leaves_nothing(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        try:
            tmp_path = pathlib.Path(tmp.name)
            preamble = 'cp() { for a in "$@"; do last="$a"; done; printf \'%s\' partial-7 > "$last"; return 1; }\n'
            proc, home, dest, helper_bytes = run_retain_rollback(tmp_path, preamble)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(os.path.lexists(str(dest)))
            self.assertFalse(any(".opos-harden-rollback." in p.name for p in home.iterdir()))
            self.assertNotIn("hardening rollback retained at ", proc.stdout)
            proc2, home2, dest2, helper_bytes2 = run_retain_rollback(tmp_path, "")
            self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
            self.assertTrue(dest2.is_file())
            self.assertFalse(dest2.is_symlink())
            self.assertEqual(dest2.read_bytes(), helper_bytes)
        finally:
            tmp.cleanup()

    def test_venv_staging_rm_and_mv_are_checked(self):
        text = read_install_sh()

        self.assertRegex(
            text,
            r'rm -rf "\$VENV"\s*\n\s*if \[ -e "\$VENV" \]',
            "must verify $VENV no longer exists after rm -rf",
        )

        self.assertRegex(
            text,
            r'if \[ -e "\$VENV" \]; then\s*\n\s*rm -rf "\$_staging_root"',
            "on rm leaving $VENV behind must clean staging root",
        )

        self.assertRegex(
            text,
            r'if \[ -e "\$VENV" \]; then[^#]*?fail ',
            "on rm failure must abort with clear message",
        )

        self.assertRegex(
            text,
            r'if ! mv "\$_staging_venv" "\$VENV"',
            "must check exit status of mv",
        )

        self.assertRegex(
            text,
            r'if ! mv "\$_staging_venv" "\$VENV"; then\s*\n\s*rm -rf "\$_staging_root"',
            "on mv failure must clean staging root",
        )

        self.assertRegex(
            text,
            r'if ! mv "\$_staging_venv" "\$VENV"; then[^#]*?fail ',
            "on mv failure must abort with clear message",
        )

        bare_mv = re.findall(r'^\s*mv "\$_staging_venv" "\$VENV"\s*$', text, re.MULTILINE)
        self.assertEqual(bare_mv, [], "bare unchecked mv must not remain")

        rm_idx = text.index('rm -rf "$VENV"')
        exist_idx = text.index('[ -e "$VENV" ]')
        mv_idx = text.index('if ! mv "$_staging_venv" "$VENV"')
        rmdir_idx = text.index('rmdir "$_staging_root"')
        self.assertLess(rm_idx, exist_idx)
        self.assertLess(exist_idx, mv_idx)
        self.assertLess(mv_idx, rmdir_idx)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_rollback_retention_rejects_replaced_staging_file(self):
        import os
        import stat
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        try:
            tmp_path = Path(tmp.name)
            victim = tmp_path / "victim"
            original_bytes = b"vic" + b"tim-keep-8"
            victim.write_bytes(original_bytes)
            os.chmod(victim, 0o640)
            mode = stat.S_IMODE(os.stat(victim).st_mode)
            preamble = 'cp() { command cp "$@" || return 1; for a in "$@"; do last="$a"; done; [ -f "$last" ] || return 1; rm -f "$last" || return 1; [ ! -e "$last" ] || return 1; ln -s "$OPOS_VICTIM" "$last" || return 1; [ -L "$last" ] || return 1; [ "$(readlink "$last")" = "$OPOS_VICTIM" ] || return 1; printf "F2160-replaced:%s\\n" "$last"; return 0; }\n'
            proc, home, dest, helper_bytes = run_retain_rollback(tmp_path, preamble)
            self.assertEqual(proc.returncode, 1)
            markers = [line for line in proc.stdout.splitlines() if "F2160-replaced:" in line]
            self.assertEqual(len(markers), 1)
            staged = markers[0].split("F2160-replaced:", 1)[1].strip()
            self.assertTrue(staged.startswith(str(home) + os.sep))
            self.assertTrue(os.path.basename(staged).startswith(".opos-harden-rollback."))
            self.assertFalse(os.path.islink(victim))
            self.assertEqual(victim.read_bytes(), original_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(victim).st_mode), mode)
            self.assertFalse(os.path.lexists(str(dest)))
            self.assertFalse(any(".opos-harden-rollback." in p.name for p in home.iterdir()))
            self.assertNotIn("hardening rollback retained at ", proc.stdout)
        finally:
            tmp.cleanup()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_force_hardening_retains_rollback_outside_checkout(self):
        install = pathlib.Path(__file__).resolve().parents[1] / "install.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            opos_home = tmp_path / "opos"
            bindir = tmp_path / "bin"
            root = tmp_path / "root"
            clone = tmp_path / "clone"
            for p in (home, opos_home, bindir):
                p.mkdir(parents=True)
            (root / "var" / "backups").mkdir(parents=True)
            (root / "var" / "backups" / "opos-harden").write_text("on\n", encoding="utf-8")
            (clone / ".git").mkdir(parents=True)
            (clone / "deploy").mkdir(parents=True)
            (clone / "install.sh").write_bytes(install.read_bytes())
            helper_src = clone / "deploy" / "harden-device.sh"
            helper_bytes = b"".join([b"#!/bin/sh\n", b"exit 0\n"])
            helper_src.write_bytes(helper_bytes)
            helper_src.chmod(helper_src.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (opos_home / "manifest").write_text(
                "launcher="
                + str(bindir / "opos")
                + "\nvenv="
                + str(opos_home / "venv")
                + "\nrepo="
                + str(clone)
                + "\n",
                encoding="utf-8",
            )
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            for name in ("ufw", "loginctl", "nft"):
                stub = fakebin / name
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            ctl_stub = fakebin / "systemctl"
            ctl_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ctl_stub.chmod(ctl_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            clean_stub = fakebin / "git"
            clean_stub.write_text(
                '#!/bin/sh\ncase "$*" in *show-toplevel*) printf \'%s\\n\' "'
                + str(clone)
                + '"\nexit 0;; esac\nexit 0\n',
                encoding="utf-8",
            )
            clean_stub.chmod(clean_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["OPOS_HOME"] = str(opos_home)
            env["OPOS_BIN_DIR"] = str(bindir)
            env["OPOS_ROOT"] = str(root)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
            if not env.get("USER"):
                env["USER"] = "opos-test-user"
            proc = subprocess.run(
                ["sh", str(clone / "install.sh"), "--uninstall", "--force"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            retained = opos_home / "opos-harden-rollback.sh"
            self.assertTrue(retained.is_file(), "force must retain rollback helper outside checkout")
            self.assertEqual(retained.read_bytes(), helper_bytes)
            self.assertTrue(os.access(str(retained), os.X_OK), "retained helper must stay executable")
            self.assertIn(str(retained), proc.stdout)
            self.assertTrue(
                (clone / "deploy" / "harden-device.sh").is_file(), "checkout must be preserved without ownership record"
            )

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retention_refuses_unverified_destination(self):
        text = read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        leaf = "".join(["opos-harden-", "rollback.sh"])
        helper_rel = "".join(["harden-device", ".sh"])
        for kind in ("regular", "symlink", "dangling"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    clone = tmp_path / "clone"
                    deploy = clone / "deploy"
                    deploy.mkdir(parents=True)
                    helper_src = deploy / helper_rel
                    helper_bytes = b"".join([b"helper-", b"bytes-9"])
                    helper_src.write_bytes(helper_bytes)
                    home = tmp_path / "home"
                    home.mkdir()
                    dest = home / leaf
                    before_bytes = None
                    before_mode = None
                    before_link = None
                    before_lstat = None
                    before_real = None
                    before_real_mode = None
                    real = tmp_path / "".join(["real-", "target"])
                    missing = tmp_path / "".join(["missing-", "target"])
                    if kind == "regular":
                        dest.write_bytes(b"".join([b"existing-", b"bytes-3"]))
                        os.chmod(str(dest), 0o600)
                        before_bytes = dest.read_bytes()
                        before_mode = stat.S_IMODE(os.stat(str(dest)).st_mode)
                    elif kind == "symlink":
                        real.write_bytes(b"".join([b"real-", b"bytes-4"]))
                        os.chmod(str(real), 0o600)
                        os.symlink(str(real), str(dest))
                        before_link = os.readlink(str(dest))
                        before_lstat = os.lstat(str(dest))
                        before_real = real.read_bytes()
                        before_real_mode = stat.S_IMODE(os.stat(str(real)).st_mode)
                    else:
                        os.symlink(str(missing), str(dest))
                        before_link = os.readlink(str(dest))
                        before_lstat = os.lstat(str(dest))
                    runner = tmp_path / "run.sh"
                    runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(home)
                    env["DRY"] = "0"
                    proc = subprocess.run(
                        ["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30
                    )
                    self.assertNotEqual(proc.returncode, 0, kind)
                    if kind == "regular":
                        self.assertFalse(dest.is_symlink())
                        self.assertEqual(dest.read_bytes(), before_bytes)
                        self.assertEqual(stat.S_IMODE(os.stat(str(dest)).st_mode), before_mode)
                    elif kind == "symlink":
                        self.assertTrue(dest.is_symlink())
                        self.assertEqual(os.readlink(str(dest)), before_link)
                        after_lstat = os.lstat(str(dest))
                        self.assertEqual(after_lstat.st_ino, before_lstat.st_ino)
                        self.assertEqual(after_lstat.st_dev, before_lstat.st_dev)
                        self.assertEqual(after_lstat.st_mode, before_lstat.st_mode)
                        self.assertEqual(real.read_bytes(), before_real)
                        self.assertEqual(stat.S_IMODE(os.stat(str(real)).st_mode), before_real_mode)
                    else:
                        self.assertTrue(dest.is_symlink())
                        self.assertFalse(dest.exists())
                        self.assertEqual(os.readlink(str(dest)), before_link)
                        after_lstat = os.lstat(str(dest))
                        self.assertEqual(after_lstat.st_ino, before_lstat.st_ino)
                        self.assertEqual(after_lstat.st_dev, before_lstat.st_dev)
                        self.assertEqual(after_lstat.st_mode, before_lstat.st_mode)
                        self.assertFalse(os.path.lexists(str(missing)))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-9"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            self.assertFalse(dest.exists() or dest.is_symlink())
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(dest.is_file())
            self.assertFalse(dest.is_symlink())
            self.assertEqual(dest.read_bytes(), helper_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(dest)).st_mode), 0o700)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retention_cleans_partial_copy_for_retry(self):
        text = read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        helper_rel = "".join(["harden-device", ".sh"])
        leaf = "".join(["opos-harden-", "rollback.sh"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"31"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            stub = stubdir / "cp"
            part = b"".join([b"par", b"tial-", b"7"])
            stub.write_text(
                '#!/bin/sh\nfor a in "$@"; do last="$a"; done\nprintf \'%s\' "'
                + part.decode()
                + '" > "$last"\nexit 1\n',
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(dest.exists() or dest.is_symlink(), "partial copy must be removed")
            stub.write_text('#!/bin/sh\nexec /bin/cp "$@"\n', encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(dest.is_file())
            self.assertFalse(dest.is_symlink())
            self.assertEqual(dest.read_bytes(), helper_bytes)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retention_rejects_directory_and_temp_replacement(self):
        text = read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        helper_rel = "".join(["harden-device", ".sh"])
        leaf = "".join(["opos-harden-", "rollback.sh"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"52"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            target_dir = tmp_path / "targetdir"
            target_dir.mkdir()
            keep = target_dir / "keep"
            keep_bytes = b"".join([b"keep-", b"bytes-3"])
            keep.write_bytes(keep_bytes)
            keep_mode = stat.S_IMODE(os.stat(str(keep)).st_mode)
            dir_mode = stat.S_IMODE(os.stat(str(target_dir)).st_mode)
            os.symlink(str(target_dir), str(dest))
            before_link = os.readlink(str(dest))
            before_lstat = os.lstat(str(dest))
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(dest.is_symlink())
            self.assertEqual(os.readlink(str(dest)), before_link)
            after_lstat = os.lstat(str(dest))
            self.assertEqual(after_lstat.st_ino, before_lstat.st_ino)
            self.assertEqual(after_lstat.st_dev, before_lstat.st_dev)
            self.assertEqual(after_lstat.st_mode, before_lstat.st_mode)
            self.assertEqual(keep.read_bytes(), keep_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(keep)).st_mode), keep_mode)
            self.assertEqual(stat.S_IMODE(os.stat(str(target_dir)).st_mode), dir_mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"53"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            dest.mkdir()
            sentinel = dest / "sentinel"
            sentinel_bytes = b"".join([b"foreign-", b"bytes-4"])
            sentinel.write_bytes(sentinel_bytes)
            os.chmod(str(dest), 0o755)
            os.chmod(str(sentinel), 0o600)
            before_mode = stat.S_IMODE(os.stat(str(dest)).st_mode)
            before_sentinel_mode = stat.S_IMODE(os.stat(str(sentinel)).st_mode)
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(dest.is_dir())
            self.assertFalse(dest.is_symlink())
            self.assertEqual(sentinel.read_bytes(), sentinel_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(dest)).st_mode), before_mode)
            self.assertEqual(stat.S_IMODE(os.stat(str(sentinel)).st_mode), before_sentinel_mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"54"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            self.assertFalse(dest.exists() or dest.is_symlink())
            foreign = tmp_path / "".join(["for", "eign"])
            foreign.write_bytes(b"".join([b"for", b"eign-bytes-", b"9"]))
            os.chmod(str(foreign), 0o640)
            foreign_bytes = foreign.read_bytes()
            foreign_mode = stat.S_IMODE(os.stat(str(foreign)).st_mode)
            marker = tmp_path / "".join(["setup-", "ok"])
            self.assertFalse(marker.exists())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            stub = stubdir / "cp"
            stub.write_text(
                "#!/bin/sh\n"
                + 'for a in "$@"; do last="$a"; done\n'
                + 'rm -f "$last" || exit 1\n'
                + 'ln -s "$OPOS_FOREIGN" "$last" || exit 1\n'
                + ': > "$OPOS_MARKER"\n'
                + "exit 1\n",
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            env["OPOS_FOREIGN"] = str(foreign)
            env["OPOS_MARKER"] = str(marker)
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertTrue(marker.is_file())
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(dest.exists() or dest.is_symlink())
            leftovers = [p for p in home.iterdir() if p.name != leaf]
            self.assertEqual(leftovers, [])
            self.assertFalse(foreign.is_symlink())
            self.assertTrue(stat.S_ISREG(os.lstat(str(foreign)).st_mode))
            self.assertEqual(foreign.read_bytes(), foreign_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(foreign)).st_mode), foreign_mode)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retention_directory_contents_unchanged(self):
        text = read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        helper_rel = "".join(["harden-device", ".sh"])
        leaf = "".join(["opos-harden-", "rollback.sh"])

        def _snapshot_tree(root):
            found = {}
            for base, dirs, files in os.walk(str(root), followlinks=False):
                for name in dirs + files:
                    full = pathlib.Path(base) / name
                    rel = str(full.relative_to(root))
                    st = os.lstat(str(full))
                    if stat.S_ISLNK(st.st_mode):
                        found[rel] = ("link", os.readlink(str(full)), stat.S_IMODE(st.st_mode))
                    elif stat.S_ISDIR(st.st_mode):
                        found[rel] = ("dir", stat.S_IMODE(st.st_mode))
                    elif stat.S_ISREG(st.st_mode):
                        found[rel] = ("file", full.read_bytes(), stat.S_IMODE(st.st_mode))
                    else:
                        found[rel] = ("other", stat.S_IMODE(st.st_mode))
            return found

        for kind in ("symlink_dir", "real_dir"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = pathlib.Path(tmp)
                    clone = tmp_path / "clone"
                    deploy = clone / "deploy"
                    deploy.mkdir(parents=True)
                    helper_src = deploy / helper_rel
                    helper_src.write_bytes(b"".join([b"helper-", b"bytes-", b"71"]))
                    home = tmp_path / "home"
                    home.mkdir()
                    dest = home / leaf
                    foreign = None
                    if kind == "symlink_dir":
                        foreign = tmp_path / "targetdir"
                        foreign.mkdir()
                        (foreign / "keep").write_bytes(b"".join([b"keep-", b"bytes-5"]))
                        os.symlink(str(foreign), str(dest))
                    else:
                        dest.mkdir()
                        (dest / "sentinel").write_bytes(b"".join([b"foreign-", b"bytes-6"]))
                        foreign = dest
                    before_home = _snapshot_tree(home)
                    before_foreign = _snapshot_tree(foreign)
                    runner = tmp_path / "run.sh"
                    runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
                    env = dict(os.environ)
                    env["OPOS_HOME"] = str(home)
                    env["DRY"] = "0"
                    proc = subprocess.run(
                        ["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30
                    )
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertEqual(_snapshot_tree(home), before_home, "home tree must match including added entries")
                    self.assertEqual(
                        _snapshot_tree(foreign), before_foreign, "foreign directory must match including added entries"
                    )

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retention_rejects_concurrent_destination_replacement(self):
        text = read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        helper_rel = "".join(["harden-device", ".sh"])
        leaf = "".join(["opos-harden-", "rollback.sh"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_bytes = b"".join([b"helper-", b"bytes-", b"91"])
            helper_src.write_bytes(helper_bytes)
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            victim = tmp_path / "".join(["vic", "tim"])
            victim_before = b"".join([b"vic", b"tim-bytes-", b"77"])
            victim.write_bytes(victim_before)
            self.assertFalse(dest.exists() or dest.is_symlink())
            stubdir = tmp_path / "stubs"
            stubdir.mkdir()
            stub = stubdir / "cp"
            stub.write_text(
                "#!/bin/sh\n" + 'ln -sf "' + str(victim) + '" "' + str(dest) + '"\n' + 'exec /bin/cp "$@"\n',
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            env["PATH"] = str(stubdir) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(victim.read_bytes(), victim_before)
            self.assertTrue(dest.is_symlink())
            self.assertEqual(os.readlink(str(dest)), str(victim))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retained_rollback_restores_state_after_checkout_removal(self):
        text = read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = _extract("opos_owned_path") + _extract("canon_file") + _extract("retain_hardening_rollback")
        for _name in ("py_ok", "find_python"):
            if _name + "() {" in text:
                helpers += _extract(_name)
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        real_src = repo_root / "deploy" / "".join(["harden-device", ".sh"])
        self.assertTrue(real_src.is_file())
        real_bytes = real_src.read_bytes()
        self.assertIn(b"--rollback", real_bytes)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            (deploy / "".join(["harden-device", ".sh"])).write_bytes(real_bytes)
            home = tmp_path / "opos-home"
            home.mkdir()
            leaf = "".join(["opos-harden-", "rollback.sh"])
            retained = home / leaf
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(retained.is_file())
            self.assertFalse(retained.is_symlink())
            self.assertEqual(retained.read_bytes(), real_bytes)
            self.assertEqual(stat.S_IMODE(os.stat(str(retained)).st_mode), 0o700)
            self.assertTrue(os.access(str(retained), os.X_OK))
            rm_proc = subprocess.run(["rm", "-rf", str(clone)], capture_output=True, text=True, timeout=30)
            self.assertEqual(rm_proc.returncode, 0)
            self.assertFalse(os.path.lexists(str(clone)))
            self.assertTrue(retained.is_file())
            self.assertEqual(retained.read_bytes(), real_bytes)
            isolated = tmp_path / "isolated"
            stamp = "20240101T000000Z"
            backup_dir = isolated / "var" / "backups" / "opos-harden" / stamp
            restore_rel = "/etc/opos/test-restore.conf"
            created_rel = "/etc/opos/created-prune.conf"
            live_path = isolated / "etc" / "opos" / "".join(["test-", "restore.conf"])
            created_path = isolated / "etc" / "opos" / "".join(["created-", "prune.conf"])
            live_bytes = b"".join([b"live-", b"bytes-", b"41"])
            backup_bytes = b"".join([b"backup-", b"bytes-", b"42"])
            created_bytes = b"".join([b"created-", b"bytes-", b"43"])
            live_path.parent.mkdir(parents=True, exist_ok=True)
            live_path.write_bytes(live_bytes)
            created_path.write_bytes(created_bytes)
            (backup_dir / "etc" / "opos").mkdir(parents=True, exist_ok=True)
            (backup_dir / "etc" / "opos" / "".join(["test-", "restore.conf"])).write_bytes(backup_bytes)
            (backup_dir / "MANIFEST").write_text(
                "restore " + restore_rel + "\ncreated " + created_rel + "\n", encoding="utf-8"
            )
            rb_env = dict(os.environ)
            rb_env["OPOS_ROOT"] = str(isolated)
            rb_proc = subprocess.run(
                ["bash", str(retained), "--rollback"], capture_output=True, text=True, env=rb_env, timeout=30
            )
            self.assertEqual(rb_proc.returncode, 0, rb_proc.stdout + rb_proc.stderr)
            self.assertEqual(live_path.read_bytes(), backup_bytes)
            self.assertFalse(os.path.lexists(str(created_path)))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retain_uses_stable_tmp_handles(self):
        text = read_install_sh()
        start = text.index("retain_hardening_rollback() {")
        end = text.index("\n}\n", start) + 3
        block = text[start:end]
        self.assertIn("O_NOFOLLOW", block)
        self.assertIn("fchmod", block)
        self.assertIn('[ ! -L "$_retain_tmp" ]', block)

        def _extract(name):
            s = text.index(name + "() {")
            e = text.index("\n}\n", s) + 3
            return text[s:e] + "\n"

        helpers = (
            _extract("opos_owned_path")
            + _extract("canon_file")
            + _extract("py_ok")
            + _extract("find_python")
            + _extract("retain_hardening_rollback")
        )
        leaf = "".join(["opos-harden-", "rollback.sh"])
        helper_rel = "".join(["harden-device", ".sh"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            helper_src = deploy / helper_rel
            helper_src.write_bytes(b"".join([b"helper-", b"bytes-5"]))
            home = tmp_path / "home"
            home.mkdir()
            dest = home / leaf
            victim = tmp_path / "".join(["vic", "tim"])
            victim.write_bytes(b"".join([b"vic", b"tim-keep-1"]))
            runner = tmp_path / "run.sh"
            script = (
                helpers
                + "cp() {\n"
                + '  last="";\n'
                + '  for a in "$@"; do last="$a"; done\n'
                + '  case "$last" in *".opos-harden-rollback."*) rm -f "$last"; ln -s "$OPOS_VICTIM" "$last";; esac\n'
                + '  command cp "$@"\n'
                + "}\n"
                + 'retain_hardening_rollback "$1"\n'
            )
            runner.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["OPOS_VICTIM"] = str(victim)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(os.path.lexists(str(dest)))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retained_rollback_instruction_quotes_path(self):
        text = read_install_sh()

        def _extract(name):
            start = text.index(name + "() {")
            end = text.index("\n}\n", start) + 3
            return text[start:end] + "\n"

        helpers = ""
        for _name in ("opos_owned_path", "canon_file", "py_ok", "find_python", "retain_hardening_rollback"):
            if _name + "() {" in text:
                helpers += _extract(_name)
        leaf = "".join(["opos-harden-", "rollback.sh"])
        helper_rel = "".join(["harden-device", ".sh"])
        sp = chr(32)
        dollar = chr(36)
        semi = chr(59)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            clone = tmp_path / "clone"
            deploy = clone / "deploy"
            deploy.mkdir(parents=True)
            (deploy / helper_rel).write_bytes(b"".join([b"#!/bin/sh\n", b"exit 0\n"]))
            home = tmp_path / ("o" + "pos" + sp + "home" + dollar + semi)
            home.mkdir()
            dest = home / leaf
            runner = tmp_path / "run.sh"
            runner.write_text(helpers + 'retain_hardening_rollback "$1"\n', encoding="utf-8")
            env = dict(os.environ)
            env["OPOS_HOME"] = str(home)
            env["DRY"] = "0"
            proc = subprocess.run(["sh", str(runner), str(clone)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            line = next((x for x in proc.stdout.splitlines() if "--rollback" in x), "")
            self.assertNotEqual(line, "")
            quoted = "'" + str(dest) + "'"
            self.assertIn(quoted, proc.stdout)
            prefix = "==> hardening active: run sudo "
            self.assertTrue(line.startswith(prefix))
            token = line[len(prefix) : -len(" --rollback")]
            self.assertEqual(token, quoted)
            check = subprocess.run(["sh", "-c", "printf '%s' " + token], capture_output=True, text=True, timeout=30)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(check.stdout, str(dest))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_retained_rollback_quoting_preserves_apostrophe_herein(self):
        import shlex

        text = read_install_sh()
        start = text.index('_retain_q="\'"')
        marker = "printf '==> hardening active: run sudo "
        end = text.index(marker, start)
        block = text[start:end]
        apos = chr(39)
        here = " herein"
        leaf = "".join(["opos-harden-", "rollback.sh"])
        cases = [
            "/tmp/opos" + apos + here + "/home/" + leaf,
            "/tmp/opos" + apos + here + "/second" + apos + "part/" + leaf,
        ]
        for path in cases:
            with self.subTest(path=path):
                env = dict(os.environ)
                env["_retain_dst"] = path
                proc = subprocess.run(
                    ["sh", "-c", block + "\nprintf '%s' \"$_retain_q\""],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(shlex.split(proc.stdout), [path])


if __name__ == "__main__":
    unittest.main()
