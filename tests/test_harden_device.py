# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for harden-device.sh."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "harden-device.sh"
LIBDIR = REPO / "deploy" / "harden.d"
KEY_LINE = "ssh-ed25519 " + "A" * 68 + " test@example"
KEY_LINE2 = "ssh-ed25519 " + "B" * 68 + " user@host"
NEED_BASH = shutil.which("bash") is not None


def run(args: list[str], root: str, stdin: str = "",
        env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run harden script with OPOS_ROOT pinned."""
    env = {**os.environ, "OPOS_ROOT": root, "TEST": "1"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", str(SCRIPT), *args], input=stdin,
                          capture_output=True, text=True, env=env)


def mode(path: Path) -> int:
    """Return permission bits of path."""
    return stat.S_IMODE(path.stat().st_mode)


def read(path: Path) -> str:
    """Read path as text."""
    return path.read_text(encoding="utf-8")


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
@unittest.skipUnless(NEED_BASH, "bash required")
class HardenDeviceTest(unittest.TestCase):
    """Harden script tests under a temp root."""

    def setUp(self) -> None:
        """Create temp root with one authorized key."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        auth = Path(self.root) / "home" / "droidian" / ".ssh" / "authorized_keys"
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text(KEY_LINE + "\n", encoding="utf-8")

    def test_check_writes_nothing(self) -> None:
        """Check mode reports without writing etc."""
        r = run(["--check"], self.root)
        self.assertEqual(r.returncode, 0)
        self.assertIn("would write", r.stdout)
        self.assertFalse((Path(self.root) / "etc").exists())

    def test_apply_needs_confirmation(self) -> None:
        """Apply aborts without yes-apply."""
        r = run(["--apply"], self.root, stdin="no\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((Path(self.root) / "etc").exists())
        r = run(["--apply"], self.root, stdin="yes-apply\n")
        self.assertEqual(r.returncode, 0)

    def test_apply_writes_hardening(self) -> None:
        """Apply writes ssh, firewall, updates files."""
        r = run(["--apply"], self.root, stdin="yes-apply\n")
        self.assertEqual(r.returncode, 0)
        base = Path(self.root)
        conf = base / "etc/ssh/sshd_config.d/10-opos-hardening.conf"
        self.assertTrue(conf.is_file())
        self.assertEqual(mode(conf), 0o644)
        text = read(conf)
        for want in ("PasswordAuthentication no", "PermitRootLogin no",
                     "AuthenticationMethods publickey", "AllowUsers droidian"):
            self.assertIn(want, text)
        nft = read(base / "etc/opos/firewall.nft")
        self.assertIn("policy drop", nft)
        lines = nft.splitlines()
        dport = [ln for ln in lines if "tcp dport 22" in ln]
        self.assertEqual(len(dport), 1)
        self.assertIn("usb0", dport[0])
        self.assertIn("rndis0", dport[0])
        self.assertNotIn("wlan", nft)
        self.assertTrue((base / "etc/systemd/system/opos-firewall.service").is_file())
        up = read(base / "etc/apt/apt.conf.d/52opos-unattended-upgrades")
        self.assertIn("-security", up)
        self.assertIn('Automatic-Reboot "false"', up)
        sysctl = read(base / "etc/sysctl.d/90-opos-hardening.conf")
        self.assertIn("kernel.kptr_restrict = 2", sysctl)

    def test_lockout_guard(self) -> None:
        """No key warns in check, dies in apply."""
        (Path(self.root) / "home/droidian/.ssh/authorized_keys").unlink()
        r = run(["--check", "--only", "ssh"], self.root)
        self.assertEqual(r.returncode, 0)
        self.assertIn("no authorized key", (r.stdout + r.stderr).lower())
        self.assertNotIn("password login off", (r.stdout + r.stderr).lower())
        r = run(["--apply", "--only", "ssh"], self.root, stdin="yes-apply\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no authorized key", (r.stdout + r.stderr).lower())
        self.assertIn("--add-key", r.stdout + r.stderr)
        p = Path(self.root) / "etc/ssh/sshd_config.d/10-opos-hardening.conf"
        self.assertFalse(p.exists())
        self.assertNotIn("password login off", r.stdout.lower())

    def test_add_key(self) -> None:
        """Add-key appends once and rejects junk."""
        key2 = KEY_LINE2
        kf = Path(self.root) / "k.pub"
        kf.write_text(key2 + "\n", encoding="utf-8")
        for _ in range(2):
            r = run(["--apply", "--only", "ssh", "--add-key", str(kf)],
                    self.root, stdin="yes-apply\n")
            self.assertEqual(r.returncode, 0)
        auth = Path(self.root) / "home/droidian/.ssh/authorized_keys"
        self.assertEqual(mode(auth), 0o600)
        self.assertEqual(read(auth).splitlines().count(key2), 1)
        bad = Path(self.root) / "bad.pub"
        bad.write_text("not a key\n", encoding="utf-8")
        r = run(["--apply", "--only", "ssh", "--add-key", str(bad)],
                self.root, stdin="yes-apply\n")
        self.assertNotEqual(r.returncode, 0)
        short = Path(self.root) / "short.pub"
        short.write_text("ssh-ed25519 AAAA test@example\n", encoding="utf-8")
        r = run(["--apply", "--only", "ssh", "--add-key", str(short)],
                self.root, stdin="yes-apply\n")
        self.assertNotEqual(r.returncode, 0)

    def test_add_key_symlink_refused(self) -> None:
        """Symlinked ssh paths are refused."""
        kf = Path(self.root) / "k.pub"
        kf.write_text(KEY_LINE2 + "\n", encoding="utf-8")
        real = Path(self.root) / "real-ssh"
        real.mkdir(parents=True, exist_ok=True)
        ssh = Path(self.root) / "home/droidian/.ssh"
        shutil.rmtree(ssh)
        ssh.symlink_to(real, target_is_directory=True)
        r = run(["--apply", "--only", "ssh", "--add-key", str(kf)],
                self.root, stdin="yes-apply\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing symlink", (r.stdout + r.stderr).lower())
        self.assertEqual(list(real.iterdir()), [])

    def test_add_key_auth_symlink_refused(self) -> None:
        """Symlinked authorized_keys is refused."""
        kf = Path(self.root) / "k.pub"
        kf.write_text(KEY_LINE2 + "\n", encoding="utf-8")
        auth = Path(self.root) / "home/droidian/.ssh/authorized_keys"
        target = Path(self.root) / "elsewhere"
        target.write_text("x\n", encoding="utf-8")
        auth.unlink()
        auth.symlink_to(target)
        r = run(["--apply", "--only", "ssh", "--add-key", str(kf)],
                self.root, stdin="yes-apply\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing symlink", (r.stdout + r.stderr).lower())
        self.assertEqual(target.read_text(encoding="utf-8"), "x\n")

    def test_add_key_notdir_refused(self) -> None:
        """Non-directory ssh path is refused."""
        kf = Path(self.root) / "k.pub"
        kf.write_text(KEY_LINE2 + "\n", encoding="utf-8")
        ssh = Path(self.root) / "home/droidian/.ssh"
        shutil.rmtree(ssh)
        ssh.write_text("file\n", encoding="utf-8")
        r = run(["--apply", "--only", "ssh", "--add-key", str(kf)],
                self.root, stdin="yes-apply\n")
        self.assertNotEqual(r.returncode, 0)

    def test_junk_key_never_disables_password(self) -> None:
        """Only valid keys allow the drop-in."""
        auth = Path(self.root) / "home/droidian/.ssh/authorized_keys"
        auth.write_text("ssh-ed25519 short test@example\n", encoding="utf-8")
        r = run(["--apply", "--only", "ssh"], self.root, stdin="yes-apply\n")
        self.assertNotEqual(r.returncode, 0)
        p = Path(self.root) / "etc/ssh/sshd_config.d/10-opos-hardening.conf"
        self.assertFalse(p.exists())

    def test_rollback(self) -> None:
        """Rollback removes created files, restores old ones."""
        base = Path(self.root)
        old = base / "etc/sysctl.d/90-opos-hardening.conf"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old", encoding="utf-8")
        r = run(["--apply"], self.root, stdin="yes-apply\n")
        self.assertEqual(r.returncode, 0)
        r = run(["--rollback"], self.root)
        self.assertEqual(r.returncode, 0)
        self.assertFalse((base / "etc/opos/firewall.nft").exists())
        self.assertFalse((base / "etc/ssh/sshd_config.d/10-opos-hardening.conf").exists())
        self.assertEqual(read(old), "old")

    def test_f001_rollback_unit_validation(self) -> None:
        base = Path(self.root) / "var/backups/opos-harden/20260101T000000Z"
        base.mkdir(parents=True, exist_ok=True)
        manifest = base / "MANIFEST"
        manifest.write_text("disabled opos-firewall.service\nenabled opos-adb-off.service\n", encoding="utf-8")
        before = manifest.read_bytes()
        r = run(["--rollback"], self.root, env_extra={"OPOS_HARDEN_TEST": "1"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("[harden] skip (test root): systemctl disable -- opos-firewall.service", r.stdout)
        self.assertIn("[harden] skip (test root): systemctl enable -- opos-adb-off.service", r.stdout)
        self.assertEqual(manifest.read_bytes(), before)
        traversal = "." + "." + "/" + "evil"
        dash_unit = "-" + "evil" + "." + "service"
        for action in ("enabled", "disabled"):
            for bad in (traversal, dash_unit):
                with self.subTest(action=action, bad=bad):
                    manifest.write_text(f"{action} {bad}\n", encoding="utf-8")
                    before = manifest.read_bytes()
                    r = run(["--rollback"], self.root, env_extra={"OPOS_HARDEN_TEST": "1"})
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("bad manifest entry", r.stderr)
                    self.assertFalse(any(line.startswith("[harden] skip (test root): systemctl") for line in r.stdout.splitlines()))
                    self.assertEqual(manifest.read_bytes(), before)

    def test_bad_env_rejected(self) -> None:
        """Bad iface and unit names fail closed."""
        r = run(["--check"], self.root, env_extra={"OPOS_USB_IFACES": "usb0;rm"})
        self.assertNotEqual(r.returncode, 0)
        r = run(["--check"], self.root, env_extra={"OPOS_DISABLE_UNITS": "x y;z"})
        self.assertNotEqual(r.returncode, 0)

    def test_firewall_missing_nft_dies_before_writing(self) -> None:
        """Missing nft dies before writing files."""
        rels = (
            "etc/opos/firewall.nft",
            "etc/opos/firewall.rules",
            "etc/opos/firewall6.rules",
            "etc/opos/firewall-legacy.sh",
            "etc/opos/firewall.backend",
            "etc/opos/firewall.backend.prev",
            "etc/systemd/system/opos-firewall.service",
        )
        env = {
            "OPOS_FW_BACKEND": "auto",
            "OPOS_HARDEN_TEST": "1",
            "OPOS_NFT": str(Path(self.root) / "missing-nft"),
            "OPOS_IPTABLES_LEGACY": str(Path(self.root) / "missing-iptables-legacy"),
            "OPOS_IP6TABLES_LEGACY": str(Path(self.root) / "missing-ip6tables-legacy"),
            "OPOS_IPTABLES_LEGACY_RESTORE": str(Path(self.root) / "missing-iptables-legacy-restore"),
            "OPOS_IP6TABLES_LEGACY_RESTORE": str(Path(self.root) / "missing-ip6tables-legacy-restore"),
        }
        r = run(["--apply", "--only", "firewall"], self.root,
                stdin="yes-apply\n", env_extra=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nft not installed", (r.stdout + r.stderr).lower())
        for rel in rels:
            self.assertFalse((Path(self.root) / rel).exists(), rel)

    def test_firewall_missing_backends_preserves_existing_artifacts(self) -> None:
        """Missing backends preserve existing artifacts."""
        rels = (
            "etc/opos/firewall.nft",
            "etc/opos/firewall.rules",
            "etc/opos/firewall6.rules",
            "etc/opos/firewall-legacy.sh",
            "etc/opos/firewall.backend",
            "etc/opos/firewall.backend.prev",
            "etc/systemd/system/opos-firewall.service",
        )
        env = {
            "OPOS_FW_BACKEND": "auto",
            "OPOS_HARDEN_TEST": "1",
            "OPOS_NFT": str(Path(self.root) / "missing-nft"),
            "OPOS_IPTABLES_LEGACY": str(Path(self.root) / "missing-iptables-legacy"),
            "OPOS_IP6TABLES_LEGACY": str(Path(self.root) / "missing-ip6tables-legacy"),
            "OPOS_IPTABLES_LEGACY_RESTORE": str(Path(self.root) / "missing-iptables-legacy-restore"),
            "OPOS_IP6TABLES_LEGACY_RESTORE": str(Path(self.root) / "missing-ip6tables-legacy-restore"),
        }
        saved = {}
        for rel in rels:
            p = Path(self.root) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel == "etc/opos/firewall.backend":
                data = b"nft\n"
            elif rel == "etc/opos/firewall.backend.prev":
                data = b"iptables-legacy\n"
            else:
                data = rel.encode("utf-8")
            p.write_bytes(data)
            p.chmod(0o640)
            saved[rel] = (p.read_bytes(), mode(p))
        r = run(["--apply", "--only", "firewall"], self.root,
                stdin="yes-apply\n", env_extra=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nft not installed", (r.stdout + r.stderr).lower())
        for rel in rels:
            p = Path(self.root) / rel
            saved_bytes, saved_mode = saved[rel]
            self.assertTrue(p.is_file(), rel)
            self.assertEqual(p.read_bytes(), saved_bytes, rel)
            self.assertEqual(mode(p), saved_mode, rel)

    def test_firewall_dhcp_requests_usb_only(self) -> None:
        """DHCP requests allowed only on USB interfaces."""
        r = run(["--apply", "--only", "firewall"], self.root,
                stdin="yes-apply\n",
                env_extra={"OPOS_USB_IFACES": "usb7 rndis7",
                           "OPOS_FW_BACKEND": "nft"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        nft = read(Path(self.root) / "etc/opos/firewall.nft")
        lines = nft.splitlines()
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip() == "chain input {")
        end = next(i for i in range(start, len(lines))
                   if lines[i].strip() == "}")
        body = [ln.strip() for ln in lines[start:end + 1]]
        expected = [
            "chain input {",
            "type filter hook input priority 0; policy drop;",
            "ct state invalid drop;",
            "ct state established,related accept;",
            'iifname "lo" accept;',
            "# SSH is reachable only over the USB network, not Wi-Fi.",
            'iifname { "usb7", "rndis7" } tcp dport 22 accept;',
            'iifname { "usb7", "rndis7" } ip6 saddr fe80::/10 udp sport 547 udp dport 546 accept;',
            "udp sport 67 udp dport 68 accept;",
            'iifname { "usb7", "rndis7" } udp sport 68 udp dport 67 accept;',
            "ip protocol icmp icmp type { echo-request, destination-unreachable, time-exceeded } limit rate 5/second accept;",
            "meta l4proto ipv6-icmp icmpv6 type { destination-unreachable, packet-too-big, time-exceeded, nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert } accept;",
            "}",
        ]
        self.assertEqual(body, expected)

    def test_fde_reports_only(self) -> None:
        """FDE step never enables encryption or secrets."""
        r = run(["--check", "--only", "fde"], self.root)
        self.assertEqual(r.returncode, 0)
        self.assertIn("NOT enabled", r.stdout)
        self.assertFalse((Path(self.root) / "etc").exists())
        combined = read(SCRIPT) + "".join(p.read_text(encoding="utf-8")
                                          for p in sorted(LIBDIR.glob("*.sh")))
        self.assertNotIn("1234", combined)
        self.assertNotIn("droidian:", combined)

    def test_services_stops_running_disabled(self) -> None:
        """Services step stops running-but-disabled units."""
        text = read(LIBDIR / "30-services.sh")
        self.assertIn("is-active", text)
        self.assertIn("stop", text)

    def test_usage_and_syntax(self) -> None:
        """Unknown flag exits 2 and syntax checks pass."""
        r = run(["--bogus"], self.root)
        self.assertEqual(r.returncode, 2)
        files = [SCRIPT, *sorted(LIBDIR.glob("*.sh"))]
        for f in files:
            r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f.name)
        if shutil.which("shellcheck") is None:
            self.skipTest("shellcheck missing")
        cmd = ["shellcheck", "-x", "-e", "SC1090,SC1091",
               *[str(f) for f in files]]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    """Run tests."""
    unittest.main()
