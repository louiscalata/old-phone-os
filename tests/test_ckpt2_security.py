# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regression fixtures for the eight ckpt2 security findings; no sockets or devices."""
import io
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from opos import panes, firstrun, web
from tests import test_duo_usb_hardening as usb, test_duo_dev_sudo as sudo, test_selftest as st

PKG = usb.PKG


class Findings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = usb.make_root(Path(self.tmp.name) / "root")

    def shell(self, code, **env):
        return subprocess.run(["sh", "-c", code], env={**os.environ, "TMPDIR": self.tmp.name, **env},
                              text=True, capture_output=True, timeout=30)

    def test_1_inherited_usb_shell_does_not_survive_the_builder_helper_call(self):
        r = self.root
        unit = r / "lib/systemd/system/telnet.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nExecStart=/usr/sbin/telnetd -l /bin/sh\n")
        wants = r / "etc/systemd/system/multi-user.target.wants"
        wants.mkdir()
        (wants / "telnet.service").symlink_to("/lib/systemd/system/telnet.service")
        (r / "etc/inetd.conf").write_text("telnet stream tcp nowait root /usr/sbin/telnetd telnetd -l /bin/sh\n")
        code = usb.block("build-image.sh", "# --- track: exposure:", "# --- end track: exposure ---")
        p = self.shell('r="$ROOT"; here="$PKG"; ' + code, ROOT=str(r), PKG=str(PKG), OPOS_DEV_USB_SHELL="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((r / "etc/opos/usb-shell.conf").read_text(), "enabled=0\n")
        self.assertFalse(os.path.lexists(wants / "telnet.service"))
        self.assertTrue((r / "etc/inetd.conf").read_text().startswith("# opos-closed "))

    def test_2_build_rejects_a_quoted_password_match(self):
        (self.root / "etc/ssh/sshd_config.d/20-local.conf").write_text(
            'Match Address ' + usb.USB_NET + '\n PasswordAuthentication "yes"\n')
        p = usb.run_build(self.root)
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_2_verifier_rejects_a_quoted_password_match(self):
        self.assertEqual(usb.run_build(self.root).returncode, 0)
        (self.root / "etc/ssh/sshd_config.d/20-local.conf").write_text(
            'Match Address ' + usb.USB_NET + '\n PasswordAuthentication "yes"\n')
        self.assertNotEqual(usb.run_verify(self.root).returncode, 0)

    def test_2_verifier_requires_the_global_include(self):
        self.assertEqual(usb.run_build(self.root).returncode, 0)
        (self.root / "etc/ssh/sshd_config").write_text("UsePAM yes\n")
        p = usb.run_verify(self.root)
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_3_verifier_rejects_authentication_disabling_defaults(self):
        (self.root / "etc/sudoers.d").mkdir()
        (self.root / "etc/sudoers").write_text("%sudo ALL=(ALL:ALL) ALL\n@includedir /etc/sudoers.d\n")
        (self.root / "etc/sudoers.d/91-local").write_text("Defaults:droidian !authenticate\n")
        code = sudo.block(*sudo.VERIFY_BLOCK)
        p = self.shell('r="$ROOT"; here="$PKG"; chome=/home/droidian; fail=0; '
                       'check() { if ! eval "$2"; then fail=1; fi; }; ' + code + '\nexit "$fail"',
                       ROOT=str(self.root), PKG=str(PKG))
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)

    def preflight(self, **env):
        # Execute the real builder through the first copy, never mount or copy an image.
        code = (PKG / "build-image.sh").read_text().split('stage=$(mktemp -d', 1)[0]
        code = code.replace('here=$(cd "$(dirname "$0")" && pwd)', 'here=' + str(PKG))
        return self.shell('id() { echo 0; }; readlink() { echo "$2"; }; '
                          'set -- missing-base untouched-output; ' + code + '\necho REACHED-COPY', **env)

    def test_4_an_owner_key_is_required_before_copying(self):
        p = self.preflight(OPOS_OWNER_SSH_PUBKEY="", OPOS_DEV_SSH_PUBKEY="", OPOS_ALLOW_NO_SSH="0")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("OPOS_OWNER_SSH_PUBKEY", p.stderr)

    def test_4_an_empty_owner_key_is_refused_before_copying(self):
        p = self.preflight(OPOS_OWNER_SSH_PUBKEY="/dev/null", OPOS_DEV_SSH_PUBKEY="", OPOS_ALLOW_NO_SSH="0")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertNotIn("REACHED-COPY", p.stdout)

    def test_4_an_empty_developer_key_cannot_substitute_for_owner_recovery(self):
        p = self.preflight(OPOS_OWNER_SSH_PUBKEY="", OPOS_DEV_SSH_PUBKEY="/dev/null", OPOS_ALLOW_NO_SSH="0")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("OPOS_OWNER_SSH_PUBKEY", p.stderr)

    def test_5_release_recipes_clear_every_dev_input(self):
        for rel in ("docs/ota-release-howto.md", "tools/opos-release.py"):
            text = (usb.ROOT / rel).read_text()
            recipe = next(line for line in text.splitlines() if "sh packaging/duo-os/build-image.sh" in line)
            for assignment in ("OPOS_RELEASE=1", "OPOS_DEV_SPAN=0", "OPOS_DEV_SUDO=0", "OPOS_DEV_USB_SHELL=0", "OPOS_DEV_SSH_PUBKEY=", "OPOS_OWNER_SSH_PUBKEY="):
                self.assertIn(assignment, recipe, rel)

    def test_6_the_original_usb_password_exception_fails_selftest(self):
        f = st.Fake()
        self.addCleanup(f.cleanup)
        f.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf", st.SSHD_DROPIN)
        c = st.ST.Ctx(str(f.root), [str(f.bin)], f.url, st.NOW, None)
        with redirect_stdout(io.StringIO()):
            st.ST.check_ssh(c)
        self.assertEqual(c.rows[0][0], "FAIL", c.rows)

    def test_6_quoted_password_match_with_a_comment_also_fails(self):
        f = st.Fake()
        self.addCleanup(f.cleanup)
        for option in ("PasswordAuthentication", "KbdInteractiveAuthentication", "ChallengeResponseAuthentication"):
            f.put("etc/ssh/sshd_config.d/20-local.conf", 'Match User droidian\n ' + option + ' "yes" # local\n')
            c = st.ST.Ctx(str(f.root), [str(f.bin)], f.url, st.NOW, None)
            with redirect_stdout(io.StringIO()):
                st.ST.check_ssh(c)
            self.assertEqual(c.rows[0][0], "FAIL", c.rows)

    def test_7_dev_sudo_adds_a_persistent_screen_warning_without_changing_html(self):
        before = panes.inject("<head></head><body>phone</body>", "n1", {"OPOS_PANES": "1"})
        original = Path.exists
        with mock.patch.object(Path, "exists", lambda p: str(p) == "/etc/sudoers.d/90-opos-dev" or original(p)):
            css = panes.css_text(env={"OPOS_PANES": "1"})
        self.assertIn('content:"DEV: passwordless sudo for droidian"', css)
        self.assertIn("position:fixed", css)
        self.assertIn("z-index:2147483647", css)

        def closed(p):  # what Path.exists does before Python 3.14 when /etc/sudoers.d is 0750 and the server is not root
            if str(p) == "/etc/sudoers.d/90-opos-dev":
                raise PermissionError(13, "Permission denied", str(p))
            return original(p)
        with mock.patch.object(Path, "exists", closed):
            plain = panes.css_text(env={"OPOS_PANES": "1"})
            self.assertIsNotNone(panes.asset("/panes.css", env={"OPOS_PANES": "1"}))
        self.assertNotIn("passwordless sudo", plain)
        self.assertEqual(css[:len(plain)], plain)
        self.assertEqual(before, panes.inject("<head></head><body>phone</body>", "n1", {"OPOS_PANES": "1"}))

    def test_8_only_dev_expectation_covers_every_emitted_id(self):
        f = st.Fake()
        self.addCleanup(f.cleanup)
        c = st.ST.Ctx(str(f.root), [str(f.bin)], f.url, st.NOW, ["dev"])
        with redirect_stdout(io.StringIO()):
            st.ST.check_dev(c)
        test_source = (usb.ROOT / "tests/test_duo_dev_sudo.py").read_text()
        expected = re.search(r'self.assertEqual\(sorted\(r.rows\), (\[.*?\])\)', test_source).group(1)
        import ast
        self.assertEqual(sorted(row[1] for row in c.rows), ast.literal_eval(expected))

    def policy(self, mode):
        return subprocess.run([sys.executable, "-I", "-B", str(PKG / "image-policy.py"), mode, str(self.root)],
                              capture_output=True, text=True, timeout=30)

    def test_2_other_include_paths_are_checked_in_both_scripts(self):
        main = self.root / "etc/ssh/sshd_config"
        main.write_text(main.read_text() + "Include extra.conf\n")
        extra = self.root / "etc/ssh/extra.conf"
        extra.write_text('Match User droidian\n KbdInteractiveAuthentication "yes"\n')
        self.assertNotEqual(usb.run_build(self.root).returncode, 0)
        self.assertNotEqual(usb.run_verify(self.root).returncode, 0)
        extra.write_text("# no password path\n")
        self.assertEqual(usb.run_build(self.root).returncode, 0)
        self.assertEqual(self.policy("ssh").returncode, 0)

    def test_2_native_sshd_rejects_invalid_config_and_disabled_public_keys(self):
        self.assertEqual(usb.run_build(self.root).returncode, 0)
        main = self.root / "etc/ssh/sshd_config"
        clean = main.read_text()
        for setting in ("NoSuchSSHOption yes", "PubkeyAuthentication no"):
            main.write_text(clean + setting + "\n")
            self.assertNotEqual(self.policy("ssh").returncode, 0, setting)

    def test_3_builder_rejects_inherited_defaults_and_external_includes(self):
        (self.root / "etc/sudoers.d").mkdir()
        (self.root / "etc/sudoers").write_text("%sudo ALL=(ALL:ALL) ALL\n@includedir /etc/sudoers.d\n")
        for setting in ("Defaults:droidian !authenticate", "Defaults exempt_group=sudo", "@include /opt/local-policy", "#include /opt/local-policy"):
            (self.root / "etc/sudoers.d/91-local").write_text(setting + "\n")
            code = sudo.block(*sudo.WRITE_BLOCK)
            p = self.shell('set -e; r="$ROOT"; here="$PKG"; ' + code,
                           ROOT=str(self.root), PKG=str(PKG), OPOS_DEV_SUDO="0")
            self.assertNotEqual(p.returncode, 0, setting + p.stderr)

    def recovery_root(self):
        # A real public key, generated only in scratch space. No key is written to the checkout.
        key = Path(self.tmp.name) / "owner"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        home = self.root / "home/droidian"
        (home / ".ssh").mkdir(parents=True)
        (self.root / "etc/passwd").write_text(f"droidian:x:{os.getuid()}:{os.getgid()}::/home/droidian:/bin/sh\n")
        (self.root / "etc/opos").mkdir()
        (self.root / "usr/sbin").mkdir()
        (self.root / "usr/sbin/sshd").write_text("fixture executable, never run\n")
        (self.root / "usr/sbin/sshd").chmod(0o755)
        units = self.root / "lib/systemd/system"
        units.mkdir(parents=True)
        (units / "ssh.service").write_text("[Service]\nExecStart=/usr/sbin/sshd -D $SSHD_OPTS\n")
        wants = self.root / "etc/systemd/system/multi-user.target.wants"
        wants.mkdir()
        (wants / "ssh.service").symlink_to("/lib/systemd/system/ssh.service")
        return key.with_suffix(".pub"), home

    def provision(self, pub, **env):
        code = usb.block("build-image.sh", "# --- recovery: BEGIN", "# --- recovery: END")
        return self.shell('set -eu; r="$ROOT"; here="$PKG"; ' + code, ROOT=str(self.root), PKG=str(PKG),
                          OPOS_OWNER_SSH_PUBKEY=str(pub), OPOS_DEV_SSH_PUBKEY="", **env)

    def test_4_recovery_repairs_home_and_rejects_bad_permissions_and_missing_startup(self):
        pub, home = self.recovery_root()
        home.chmod(0o777)
        p = self.provision(pub)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(home.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.policy("recovery").returncode, 0)
        for path in (home, home / ".ssh", home / ".ssh/authorized_keys"):
            before = path.stat().st_mode & 0o777
            path.chmod(0o777)
            self.assertNotEqual(self.policy("recovery").returncode, 0, str(path))
            path.chmod(before)
        (self.root / "etc/systemd/system/multi-user.target.wants/ssh.service").unlink()
        self.assertNotEqual(self.policy("recovery").returncode, 0)

    def test_4_explicit_no_ssh_is_required_and_has_no_authorized_key(self):
        p = self.preflight(OPOS_OWNER_SSH_PUBKEY="", OPOS_DEV_SSH_PUBKEY="", OPOS_ALLOW_NO_SSH="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("REACHED-COPY", p.stdout)
        pub, home = self.recovery_root()
        self.assertEqual(self.provision(pub).returncode, 0)
        p = self.provision("", OPOS_ALLOW_NO_SSH="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((home / ".ssh/authorized_keys").exists())
        self.assertTrue((self.root / "etc/opos/no-ssh").is_file())

    def test_4_recovery_refuses_linked_ancestors_before_writing_keys(self):
        pub, home = self.recovery_root()
        outside = Path(self.tmp.name) / "outside"
        home.rename(outside)
        home.symlink_to(outside, target_is_directory=True)
        p = self.provision(pub)
        self.assertNotEqual(p.returncode, 0, p.stderr)
        self.assertFalse((outside / ".ssh/authorized_keys").exists())

    def test_5_release_refuses_inherited_dev_inputs_and_installed_dev_files(self):
        for name, value in (("OPOS_DEV_SSH_PUBKEY", "/dev/null"), ("OPOS_DEV_USB_SHELL", "1"), ("OPOS_DEV_SUDO", "1"), ("OPOS_DEV_SPAN", "1")):
            p = self.preflight(OPOS_RELEASE="1", OPOS_ALLOW_NO_SSH="1", **{name: value})
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertIn("development inputs", p.stderr)
        (self.root / "etc/opos").mkdir()
        (self.root / "etc/opos/usb-shell.conf").write_text("enabled=0\n")
        (self.root / "etc/opos/build-mode").write_text("release\n")
        self.assertEqual(self.policy("release").returncode, 0)
        for rel in ("etc/default/opos-kiosk", "etc/sudoers.d/90-opos-dev", "etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf",
                    "etc/systemd/system/multi-user.target.wants/opos-clock-set.path"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stale dev state\n")
            self.assertNotEqual(self.policy("release").returncode, 0, rel)
            path.unlink()

    def test_5_owner_provisioning_replaces_inherited_keys_without_enabling_devtools(self):
        pub, home = self.recovery_root()
        (home / ".ssh/authorized_keys").write_text("inherited developer key\n")
        code = (PKG / "build-image.sh").read_text().split("# --- recovery: BEGIN\n", 1)[1].split("# --- dev span: BEGIN", 1)[0]
        (self.root / "etc/default").mkdir()
        (self.root / "etc/default/opos-kiosk").write_text("OPOS_KIOSK_DEBUG_PORT=9222\n")
        p = self.shell('set -eu; r="$ROOT"; here="$PKG"; ' + code, ROOT=str(self.root), PKG=str(PKG),
                       OPOS_OWNER_SSH_PUBKEY=str(pub), OPOS_DEV_SSH_PUBKEY="")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((home / ".ssh/authorized_keys").read_text(), pub.read_text())
        self.assertFalse((self.root / "etc/default/opos-kiosk").exists())


class SshInMemory(st.Ssh):
    """Run the existing SSH assertions without starting their usual loopback server."""
    def setUp(self):
        self.f = st.Fake()
        self.addCleanup(self.f.cleanup)

    def run_st(self):
        c = st.ST.Ctx(str(self.f.root), [str(self.f.bin)], self.f.url, st.NOW, None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            st.ST.check_ssh(c)
        return st.Result(int(any(row[0] == "FAIL" for row in c.rows)), buf.getvalue())


class DevInMemory(sudo.SelftestWarning):
    """Exercise the actual --only dev assertions with just the read-only dev check."""
    def setUp(self):
        self.f = st.Fake()
        self.addCleanup(self.f.cleanup)

    def run_st(self, *args):
        only = [args[i + 1] for i, value in enumerate(args) if value == "--only"]
        c = st.ST.Ctx(str(self.f.root), [str(self.f.bin)], self.f.url, st.NOW, only)
        buf = io.StringIO()
        with redirect_stdout(buf):
            st.ST.check_dev(c)
        return st.Result(int(any(row[0] == "FAIL" for row in c.rows)), buf.getvalue())


class PageBytes(unittest.TestCase):
    def test_page_and_home_digests_match_the_pre_fix_copy_without_sockets(self):
        switches = {"OPOS_OSK": "1", "OPOS_NETWORK": "1", "OPOS_FIRSTRUN": "1", "OPOS_MONITOR": "1", "OPOS_PANES": "1", "OPOS_SPAN": "675,42"}

        def home(enabled):
            env = dict.fromkeys(switches, "")
            env.update(enabled)
            with tempfile.TemporaryDirectory() as config, mock.patch.dict(os.environ, dict(env, OPOS_CONFIG_DIR=config)), \
                    mock.patch.object(web.secrets, "token_urlsafe", return_value="n" * 22):
                undo = firstrun.install(web)
                try:
                    h = web.Handler.__new__(web.Handler)
                    h.headers, h.port, h.command, h.path = {"Host": "127.0.0.1:8765"}, 8765, "GET", "/"
                    h.token, h.span = "t" * 43, web.parse_span(enabled["OPOS_SPAN"]) if "OPOS_SPAN" in enabled else None
                    h.wfile = io.BytesIO()
                    headers, status = {}, []
                    h.send_response, h.send_header, h.end_headers = status.append, headers.__setitem__, lambda: None
                    h._get()
                    body = h.wfile.getvalue()
                    self.assertEqual(status, [200])
                    self.assertEqual(int(headers["Content-Length"]), len(body))
                    return body
                finally:
                    if undo:
                        undo()

        with mock.patch.dict(os.environ, dict.fromkeys(switches, "")):
            bodies = [web.page("t" * 43, "n" * 22).encode(),
                      web.page("t" * 43, "n" * 22, span=web.parse_span("675,42")).encode(), home({}), home(switches)]
        expected = [(100696, "1592ee9cdc8ec87fdc27d723ff8b65aa667363e75f331f65102e2f2082c395db"),
                    (100739, "eddba922a9e66c97aa33ae34bb2d88b7de9cfa2d9f3802e266ab14379d62621b"),
                    (100768, "41f98e6e49a6ae0842529146c43e0a90e4d753c4abacfaa8d3e36d086a51f076"),
                    (101812, "67d84fe84aa8073750106eb516df262a89d495794f6e003069e2d3a30b71bc9e")]
        self.assertEqual([(len(body), hashlib.sha256(body).hexdigest()) for body in bodies], expected)
