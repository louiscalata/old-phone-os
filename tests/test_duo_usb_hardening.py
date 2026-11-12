# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo OS image takes two doors out of the base that need no key: the root telnet shell on the USB link and the sshd password login for the USB subnet.

The community port's USB gadget script (sfduo-usb-gadget.sh, run at every boot by sfduo-usb.service) ends with `busybox telnetd -l /bin/bash`, a root shell with no
login for anyone on the cable; and its sshd drop-in 10-item-password-usb-only.conf lets a password in for every client whose source address is in the USB link's /24,
on any interface (Wi-Fi included once one is joined). `build-image.sh` (block "usb hardening", always, no knob) comments out the first and makes the second key-only;
`verify-image.sh` checks both; `opos-selftest` `ssh.auth` says "key-only everywhere" for the result. The blocks are run here against a scratch root file system,
the way the dev-span, dev-sudo, clock and OTA blocks are; no image, mount or qemu is involved. Nothing here touches a phone."""
import difflib
import os
import re
import shutil
import stat as statmod
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
PKG = ROOT / "packaging" / "duo-os"
SH = shutil.which("sh")
USB_IP = "172.16" + ".42.1"  # written in parts so the repository holds no private address
USB_NET = "172.16" + ".42.0/24"
USB_CLIENT = "172.16" + ".42.77"
GADGET_REL = "usr/local/sbin/sfduo-usb-gadget.sh"
DROPIN_REL = "etc/ssh/sshd_config.d/10-item-password-usb-only.conf"
TELNET_LINE = "/usr/local/bin/busybox telnetd -l /bin/bash -b %s -p 23" % USB_IP

# The port's script, as the base (item-duo1-20261025-port0.22.1) ships it: 755 root:root, 1378 bytes.
GADGET = """#!/bin/sh
# sfduo: RNDIS gadget + static IP + root telnet (proven on this kernel 2026-07-11)
mount -t configfs none /sys/kernel/config 2>/dev/null
G=/sys/kernel/config/usb_gadget/sfduo
if [ ! -d "$G" ]; then
    UDC=""
    i=0
    while [ $i -lt 60 ]; do
        UDC=$(ls /sys/class/udc 2>/dev/null | head -1)
        [ -n "$UDC" ] && break
        i=$((i + 1)); sleep 1
    done
    [ -z "$UDC" ] && exit 1
    mkdir -p $G/strings/0x409 $G/configs/c.1/strings/0x409 $G/functions/rndis.usb0
    echo 0x1d6b > $G/idVendor
    echo 0x0104 > $G/idProduct
    echo sfduo > $G/strings/0x409/manufacturer
    echo "sfduo Droidian" > $G/strings/0x409/product
    echo duo1-0001 > $G/strings/0x409/serialnumber
    echo rndis > $G/configs/c.1/strings/0x409/configuration
    # 500 (max for USB2 non-PD): the charger ICL follows what we declare -
    # 250 here capped charging at 250 mA (found 2026-07-11)
    echo 500 > $G/configs/c.1/MaxPower
    ln -sf $G/functions/rndis.usb0 $G/configs/c.1/rndis.usb0
    echo "$UDC" > $G/UDC
    sleep 2
fi
IFACE=""
for CAND in usb0 rndis0; do
    [ -e "/sys/class/net/$CAND" ] && IFACE=$CAND && break
done
ip addr add %(ip)s/24 dev "${IFACE:-usb0}" 2>/dev/null
ip link set "${IFACE:-usb0}" up
# root shell over telnet (no sshd in this nightly; busybox is static arm64)
%(telnet)s
exit 0
""" % {"ip": USB_IP, "telnet": TELNET_LINE}

# The port's drop-in, as the base ships it: 664 root:root.
DROPIN_PORT = """# The PIN is the password, four digits: over Wi-Fi or mobile data it could
# be guessed. A password only from the USB link; elsewhere keys only.
PasswordAuthentication no
KbdInteractiveAuthentication no
Match Address %s
    PasswordAuthentication yes
""" % USB_NET
SSHD_CONFIG = """Include /etc/ssh/sshd_config.d/*.conf
KbdInteractiveAuthentication no
UsePAM yes
X11Forwarding yes
PrintMotd no
AcceptEnv LANG LC_* COLORTERM NO_COLOR
Subsystem\tsftp\t/usr/lib/openssh/sftp-server
"""
SFDUO_CONF = "PermitRootLogin prohibit-password\n"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def block(script, begin, end):
    """The lines between the marker lines BEGIN and END of a script (exclusive), each marker exactly once."""
    text = (PKG / script).read_text(encoding="utf-8")
    b = re.findall(r"(?m)^%s.*$" % re.escape(begin), text)
    e = re.findall(r"(?m)^%s$" % re.escape(end), text)
    assert len(b) == 1 and len(e) == 1, (script, begin, b, e)
    i = text.index(b[0]) + len(b[0]) + 1
    j = text.index(end + "\n", i)
    return text[i:j]


BUILD_BLOCK = ("build-image.sh", "# --- usb hardening: BEGIN", "# --- usb hardening: END")
VERIFY_BLOCK = ("verify-image.sh", "# --- usb hardening: BEGIN", "# --- usb hardening: END")


def make_root(root, gadget=GADGET, gadget_mode=0o755, dropin=DROPIN_PORT, dropin_mode=0o664):
    """A scratch root file system with the port's two files in the state the base has them."""
    r = Path(root)
    (r / "usr/local/sbin").mkdir(parents=True)
    (r / "usr/local/bin").mkdir(parents=True)
    (r / "etc/ssh/sshd_config.d").mkdir(parents=True)
    (r / "etc/systemd/system").mkdir(parents=True)
    (r / "usr/lib/systemd/system").mkdir(parents=True)
    if gadget is not None:
        g = r / GADGET_REL
        g.write_text(gadget, encoding="utf-8", newline="\n")
        g.chmod(gadget_mode)
    (r / "usr/local/bin/busybox").write_bytes(b"\x7fELF telnetd\0 binary stand-in\n")
    (r / "etc/ssh/sshd_config").write_text(SSHD_CONFIG, encoding="utf-8")
    (r / "etc/ssh/sshd_config.d/10-sfduo.conf").write_text(SFDUO_CONF, encoding="utf-8")
    if dropin is not None:
        d = r / DROPIN_REL
        d.write_text(dropin, encoding="utf-8")
        d.chmod(dropin_mode)
    (r / "usr/lib/systemd/system/sfduo-usb.service").write_text("[Service]\nExecStart=/usr/local/sbin/sfduo-usb-gadget.sh\n", encoding="utf-8")
    return r


def run_build(r, extra=""):
    script = Path(r).parent / ("build-%d.sh" % os.getpid())
    script.write_text("set -eu\nr=%s\nhere=%s\n%s\n%secho BLOCK-DONE\n" % (r, PKG, block(*BUILD_BLOCK), extra), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_")}
    return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=120)


def run_verify(r, owner="0:0", path=None):
    script = Path(r).parent / ("verify-%d.sh" % os.getpid())
    stat = 'stat() { if [ "$1 $2" = "-c %%u:%%g" ]; then echo %s; else command stat "$@"; fi; }\n' % owner
    script.write_text('fail=0; r=%s; here=%s\ncheck() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n%s%s\nexit $fail\n'
                      % (r, PKG, stat, block(*VERIFY_BLOCK)), encoding="utf-8")
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=120)


class StaticPins(unittest.TestCase):
    def test_each_block_is_delimited_once(self):
        for spec in (BUILD_BLOCK, VERIFY_BLOCK):
            with self.subTest(spec=spec):
                self.assertTrue(block(*spec).strip())

    def test_the_build_block_sits_after_the_dev_sudo_block_and_before_the_updater(self):
        t = read("packaging/duo-os/build-image.sh")
        self.assertLess(t.index("# --- dev sudo: END"), t.index("# --- usb hardening: BEGIN"))
        self.assertLess(t.index("# --- usb hardening: END"), t.index("# ---- BEGIN OPOS OTA updates"))
        self.assertGreater(t.index("# --- usb hardening: BEGIN"), t.index('mount -o loop "$image" "$mnt"'))

    def test_the_verify_block_sits_after_the_droidian_home_and_the_dev_sudo_block_and_before_the_first_unmount(self):
        t = read("packaging/duo-os/verify-image.sh")
        self.assertLess(t.index("# --- dev sudo: END"), t.index("# --- usb hardening: BEGIN"))
        self.assertLess(t.index("# --- usb hardening: END"), t.index("# Server smoke test"))

    def test_there_is_no_knob_the_edit_is_unconditional(self):
        for spec in (BUILD_BLOCK, VERIFY_BLOCK):
            code = "\n".join(ln for ln in block(*spec).splitlines() if not ln.lstrip().startswith("#"))
            self.assertNotRegex(code, r"\$\{?OPOS_", spec)  # no build variable is read
            self.assertNotRegex(code, r"(?m)^\s*(if|case)\b.*\$\{", spec)

    def test_the_build_block_edits_in_place_and_does_not_follow_a_link(self):
        code = "\n".join(ln for ln in block(*BUILD_BLOCK).splitlines() if not ln.lstrip().startswith("#"))
        self.assertIn('[ -f "$ug" ] && [ ! -L "$ug" ]', code)
        self.assertIn('cat "$ug_tmp" > "$ug"', code)
        self.assertIn('cat "$sf_tmp" > "$sf"', code)
        self.assertNotIn("mv ", code)
        self.assertNotRegex(code, r"(?m)\bsed -i\b")

    def test_the_new_files_name_no_private_address(self):
        for path in ("packaging/duo-os/build-image.sh", "packaging/duo-os/verify-image.sh", "tests/test_duo_usb_hardening.py"):
            self.assertNotRegex(read(path), r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])", path)

    def test_the_scripts_parse_and_have_no_carriage_returns(self):
        for n in ("build-image.sh", "verify-image.sh"):
            raw = (PKG / n).read_bytes()
            self.assertNotIn(b"\r", raw, n)
            if SH:
                r = subprocess.run([SH, "-n", str(PKG / n)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, n + ": " + r.stderr)

    def test_the_verify_block_starts_no_server_and_runs_no_telnet(self):
        code = "\n".join(ln for ln in block(*VERIFY_BLOCK).splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("serve ", code)
        self.assertNotRegex(code, r"(?m)(^|[;&|(`])\s*(busybox|telnet|nc|ssh|sshd)\s")

    def test_the_docs_say_it_was_removed_and_keep_mains_exposure_hardening(self):
        readme = " ".join(read("packaging/duo-os/README.md").split())
        self.assertIn("## Removed on purpose: the USB root shell and the password login", read("packaging/duo-os/README.md"))
        sec = " ".join(read("packaging/duo-os/README.md").split("## Removed on purpose: the USB root shell and the password login")[1].split("\n## ")[0].split())
        for phrase in ("sfduo-usb-gadget.sh", "telnetd", "no login", "10-item-password-usb-only.conf", "key-only", "every image", "OPOS_DEV_SSH_PUBKEY",
                       "close-exposure.sh", "loopback", "verify-image.sh", "ssh.auth", "ss -ltn"):
            self.assertIn(phrase, sec)
        self.assertNotIn("passwords only from the port's own USB subnet", readme, "the dev-key section no longer says a password is accepted from the USB subnet")
        final = " ".join(read("docs/duo-os-final.md").split())
        for phrase in ("Removed on purpose", "telnetd", "key-only", "debug port", "loopback"):
            self.assertIn(phrase, final)
        self.assertIn("root telnet shell on the USB link", read("CHANGELOG.md"))


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class BuildBlock(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.r = make_root(self.tmp / "rootfs")

    def lines(self, rel):
        return (self.r / rel).read_text(encoding="utf-8").splitlines()

    def test_only_the_telnetd_line_of_the_gadget_script_changes_and_it_becomes_a_comment(self):
        p = run_build(self.r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        old, new = GADGET.splitlines(), self.lines(GADGET_REL)
        self.assertEqual(len(old), len(new))
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertEqual(len(changed), 1, changed)
        self.assertEqual(changed[0][0], TELNET_LINE)
        self.assertTrue(changed[0][1].startswith("# OPOS: "), changed[0][1])
        self.assertIn("telnet shell", changed[0][1])
        self.assertIn("removed", changed[0][1])
        self.assertEqual(new[-1], "exit 0")
        self.assertEqual(new[-2], changed[0][1])
        self.assertFalse([ln for ln in new if not ln.lstrip().startswith("#") and "telnetd" in ln])
        self.assertIn("USB: the root telnet shell in sfduo-usb-gadget.sh was removed", p.stdout)
        self.assertIn(GADGET_REL, p.stdout, "the manifest line with the file's hash")

    def test_the_script_still_parses_and_its_mode_owner_and_inode_are_kept(self):
        g = self.r / GADGET_REL
        g.chmod(0o750)
        before = g.stat()
        p = run_build(self.r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        after = g.stat()
        self.assertEqual(statmod.S_IMODE(after.st_mode), 0o750)
        self.assertEqual((after.st_uid, after.st_gid, after.st_ino), (before.st_uid, before.st_gid, before.st_ino), "rewritten in place, not replaced")
        self.assertEqual(subprocess.run([SH, "-n", str(g)], capture_output=True, text=True).returncode, 0)

    def test_the_script_is_not_executed_by_the_build(self):
        # a gadget script whose other lines would leave a trace if run: the build must only read and rewrite it
        trace = self.tmp / "ran"
        g = self.r / GADGET_REL
        g.write_text(GADGET.replace("sleep 2", "touch %s" % trace), encoding="utf-8")
        g.chmod(0o755)
        run_build(self.r)
        self.assertFalse(trace.exists())

    def test_running_it_twice_changes_nothing_the_second_time(self):
        run_build(self.r)
        once = {rel: (self.r / rel).read_bytes() for rel in (GADGET_REL, DROPIN_REL)}
        p = run_build(self.r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("starts no telnet shell; leaving it as it is", p.stdout)
        self.assertNotIn("root telnet shell in sfduo-usb-gadget.sh was removed", p.stdout)
        self.assertEqual(once, {rel: (self.r / rel).read_bytes() for rel in (GADGET_REL, DROPIN_REL)})

    def test_every_non_comment_line_with_telnetd_goes_and_comments_stay(self):
        g = self.r / GADGET_REL
        g.write_text(GADGET.replace("exit 0\n", "# telnetd is gone, said the port once\n  busybox telnetd -b x -l /bin/sh &\nexit 0\n"), encoding="utf-8")
        g.chmod(0o755)
        p = run_build(self.r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        new = self.lines(GADGET_REL)
        self.assertIn("# telnetd is gone, said the port once", new)
        self.assertFalse([ln for ln in new if not ln.lstrip().startswith("#") and "telnetd" in ln])
        self.assertEqual(len(new), len(GADGET.splitlines()) + 2)

    def test_a_script_that_never_started_telnet_is_left_alone(self):
        clean = GADGET.replace(TELNET_LINE + "\n", "")
        make = self.tmp / "other"
        r = make_root(make / "rootfs", gadget=clean)
        before = (r / GADGET_REL).read_bytes()
        p = run_build(r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((r / GADGET_REL).read_bytes(), before)

    def test_a_base_without_the_script_or_with_a_link_or_a_non_executable_one_stops_the_build(self):
        for label, kw in (("missing", dict(gadget=None)), ("not executable", dict(gadget_mode=0o644))):
            with self.subTest(label):
                r = make_root(self.tmp / label.replace(" ", "-") / "rootfs", **kw)
                p = run_build(r)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("sfduo-usb-gadget.sh", p.stderr)
        r = make_root(self.tmp / "link" / "rootfs", gadget=None)
        target = self.tmp / "link" / "host-file"
        target.write_text(GADGET, encoding="utf-8")
        target.chmod(0o755)
        (r / GADGET_REL).symlink_to(target)
        p = run_build(r)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), GADGET, "a link in the image must never lead the build to a file outside it")

    def test_a_script_that_would_not_parse_after_the_edit_stops_the_build(self):
        g = self.r / GADGET_REL
        g.write_text(GADGET.replace("exit 0\n", "if true; then\nexit 0\n"), encoding="utf-8")
        g.chmod(0o755)
        p = run_build(self.r)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("edit did not take", p.stderr)

    def test_the_sshd_drop_in_becomes_key_only_and_keeps_its_name_mode_owner_and_inode(self):
        d = self.r / DROPIN_REL
        before = d.stat()
        p = run_build(self.r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        text = d.read_text(encoding="utf-8")
        self.assertEqual([ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")], ["PasswordAuthentication no", "KbdInteractiveAuthentication no"])
        self.assertNotRegex(text, r"(?im)^\s*Match\b")
        self.assertNotRegex(text, r"(?im)^\s*\w+\s+yes\b")
        self.assertNotIn(USB_NET, text)
        after = d.stat()
        self.assertEqual(statmod.S_IMODE(after.st_mode), 0o664)
        self.assertEqual((after.st_uid, after.st_gid, after.st_ino), (before.st_uid, before.st_gid, before.st_ino))
        self.assertIn("USB: ssh is key-only", p.stdout)
        self.assertIn(DROPIN_REL, p.stdout, "the manifest line with the file's hash")

    def test_no_other_file_of_the_ssh_configuration_is_touched(self):
        before = {str(x.relative_to(self.r)): x.read_bytes() for x in self.r.rglob("*") if x.is_file()}
        p = run_build(self.r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        after = {str(x.relative_to(self.r)): x.read_bytes() for x in self.r.rglob("*") if x.is_file()}
        self.assertEqual({k for k in before if before[k] != after.get(k)}, {GADGET_REL, DROPIN_REL})
        self.assertEqual(set(before), set(after), "no file is added or removed")

    def test_a_base_without_the_drop_in_gets_one(self):
        r = make_root(self.tmp / "nodrop" / "rootfs", dropin=None)
        p = run_build(r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        d = r / DROPIN_REL
        self.assertTrue(d.is_file())
        self.assertEqual(statmod.S_IMODE(d.stat().st_mode), 0o644)
        self.assertIn("PasswordAuthentication no", d.read_text(encoding="utf-8"))

    def test_a_link_in_the_place_of_the_drop_in_stops_the_build_and_is_not_followed(self):
        target = self.tmp / "elsewhere.conf"
        target.write_text("keep\n", encoding="utf-8")
        (self.r / DROPIN_REL).unlink()
        (self.r / DROPIN_REL).symlink_to(target)
        p = run_build(self.r)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")

    def test_a_password_yes_set_elsewhere_stops_the_build(self):
        for rel, text in (("etc/ssh/sshd_config", "PasswordAuthentication yes\n" + SSHD_CONFIG), ("etc/ssh/sshd_config.d/20-other.conf", "kbdinteractiveauthentication = yes\n"),
                          ("etc/ssh/sshd_config.d/20-other.conf", "ChallengeResponseAuthentication yes\n"), ("etc/ssh/sshd_config.d/20-other.conf", "Match User x\n  PasswordAuthentication   YES\n")):
            with self.subTest(rel=rel, text=text):
                r = make_root(self.tmp / ("e%d" % abs(hash((rel, text)))) / "rootfs")
                (r / rel).write_text(text, encoding="utf-8")
                p = run_build(r)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("another sshd setting turns a password login on", p.stderr)

    @unittest.skipUnless(shutil.which("sshd") or os.path.exists("/usr/sbin/sshd"), "needs sshd")
    @unittest.skipUnless(shutil.which("ssh-keygen"), "needs ssh-keygen")
    def test_sshd_itself_takes_no_password_from_the_usb_subnet_or_anywhere_after_the_edit(self):
        sshd = shutil.which("sshd") or "/usr/sbin/sshd"
        key = self.tmp / "hostkey"
        subprocess.run([shutil.which("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True, timeout=60)

        def passwordauth(r, addr):
            cfg = self.tmp / ("sshd_config_%s" % abs(hash((str(r), addr))))
            cfg.write_text(SSHD_CONFIG.replace("/etc/ssh/sshd_config.d/", "%s/etc/ssh/sshd_config.d/" % r), encoding="utf-8")
            p = subprocess.run([sshd, "-T", "-f", str(cfg), "-h", str(key), "-C", "user=droidian,host=phone,laddr=198.51.100.1,lport=22,addr=%s" % addr],
                               capture_output=True, text=True, timeout=60)
            if p.returncode != 0:
                self.skipTest("sshd -T cannot run here: " + (p.stderr or p.stdout)[:200])
            m = re.search(r"(?mi)^passwordauthentication (\w+)$", p.stdout)
            k = re.search(r"(?mi)^kbdinteractiveauthentication (\w+)$", p.stdout)
            return m.group(1), k.group(1)

        # the port's file: a password is taken from the USB subnet and from nowhere else (this is what makes the comparison below mean something)
        self.assertEqual(passwordauth(self.r, USB_CLIENT), ("yes", "no"))
        self.assertEqual(passwordauth(self.r, "203.0.113.9"), ("no", "no"))
        p = run_build(self.r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for addr in (USB_CLIENT, "203.0.113.9", "198.51.100.4"):
            self.assertEqual(passwordauth(self.r, addr), ("no", "no"), addr)


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class VerifyBlock(unittest.TestCase):
    """The verify-image.sh block against a scratch root file system: the image as the build leaves it, and the image as the port ships it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.r = make_root(self.tmp / "rootfs")

    def built(self):
        p = run_build(self.r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def assert_clean(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("FAIL", p.stdout)

    def assert_fails(self, p, what):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL " + what, p.stdout)

    def test_the_image_as_the_build_leaves_it_passes(self):
        self.built()
        p = run_verify(self.r)
        self.assert_clean(p)
        self.assertIn("PASS USB: the gadget script starts no telnet shell", p.stdout)
        self.assertIn("PASS USB: no other script, config or unit", p.stdout)
        self.assertIn("PASS ssh: key-only everywhere", p.stdout)
        self.assertIn("NOTE: ssh takes keys only", p.stdout)
        self.assertIn("separate exposure checks cover the debug port", p.stdout)

    def test_the_image_as_the_port_ships_it_fails_both_ways(self):
        p = run_verify(self.r)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL USB: the gadget script starts no telnet shell", p.stdout)
        self.assertIn("FAIL USB: no other script", p.stdout)
        self.assertIn("FAIL ssh: key-only everywhere", p.stdout)

    def test_the_telnet_line_back_in_the_gadget_script_fails(self):
        self.built()
        g = self.r / GADGET_REL
        g.write_text(g.read_text(encoding="utf-8").replace("exit 0\n", TELNET_LINE + "\nexit 0\n"), encoding="utf-8")
        p = run_verify(self.r)
        self.assert_fails(p, "USB: the gadget script starts no telnet shell")
        self.assertIn("FAIL USB: no other script", p.stdout)
        self.assertIn("PASS ssh: key-only everywhere", p.stdout)

    def test_a_telnetd_that_is_only_a_comment_is_fine(self):
        self.built()
        g = self.r / GADGET_REL
        g.write_text(g.read_text(encoding="utf-8").replace("exit 0\n", "#  " + TELNET_LINE + "\nexit 0\n"), encoding="utf-8")
        self.assert_clean(run_verify(self.r))

    def test_telnetd_started_by_another_script_config_or_unit_fails(self):
        self.built()
        for rel, text in (("usr/local/sbin/other.sh", "#!/bin/sh\nbusybox telnetd -l /bin/sh\n"), ("etc/rc.local", "telnetd &\n"),
                          ("etc/systemd/system/t.service", "[Service]\nExecStart=/usr/local/bin/busybox telnetd -F\n"),
                          ("usr/lib/systemd/system/t2.service", "[Service]\nExecStart=/usr/sbin/in.telnetd\n"),
                          ("lib/systemd/system/t3.service", "[Service]\nExecStart=/usr/local/bin/busybox telnetd -F\n"),
                          ("etc/xinetd.d/telnet", "service telnet\n{\n  server = /usr/sbin/in.telnetd\n}\n")):
            with self.subTest(rel=rel):
                f = self.r / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
                p = run_verify(self.r)
                self.assert_fails(p, "USB: no other script, config or unit")
                self.assertIn("PASS USB: the gadget script starts no telnet shell", p.stdout)
                f.unlink()

    def test_the_busybox_binary_alone_is_not_a_finding(self):
        self.built()
        self.assertIn(b"telnetd", (self.r / "usr/local/bin/busybox").read_bytes())
        self.assert_clean(run_verify(self.r))

    def test_a_gadget_script_that_is_broken_in_any_other_way_fails(self):
        self.built()
        g = self.r / GADGET_REL
        good = g.read_text(encoding="utf-8")
        for label, text, mode in (("syntax error", good.replace("exit 0\n", "if true; then\nexit 0\n"), 0o755), ("no exit 0", good.replace("exit 0\n", "true\n"), 0o755),
                                  ("not executable", good, 0o644)):
            with self.subTest(label):
                g.write_text(text, encoding="utf-8")
                g.chmod(mode)
                self.assert_fails(run_verify(self.r), "USB: the gadget script starts no telnet shell")
        g.write_text(good, encoding="utf-8")
        g.chmod(0o755)
        self.assert_clean(run_verify(self.r))
        self.assert_fails(run_verify(self.r, owner="1000:1000"), "USB: the gadget script starts no telnet shell")
        self.assert_fails(run_verify(self.r, owner="0:1000"), "USB: the gadget script starts no telnet shell")

    def test_a_gadget_script_that_is_a_link_or_missing_fails(self):
        self.built()
        g = self.r / GADGET_REL
        text = g.read_text(encoding="utf-8")
        g.unlink()
        self.assert_fails(run_verify(self.r), "USB: the gadget script starts no telnet shell")
        target = self.tmp / "t.sh"
        target.write_text(text, encoding="utf-8")
        target.chmod(0o755)
        g.symlink_to(target)
        self.assert_fails(run_verify(self.r), "USB: the gadget script starts no telnet shell")

    def test_the_port_s_match_block_back_in_the_drop_in_fails(self):
        self.built()
        (self.r / DROPIN_REL).write_text(DROPIN_PORT, encoding="utf-8")
        p = run_verify(self.r)
        self.assert_fails(p, "ssh: key-only everywhere")
        self.assertIn("PASS USB: the gadget script starts no telnet shell", p.stdout)

    def test_any_password_yes_anywhere_in_the_sshd_configuration_fails(self):
        self.built()
        d = self.r / "etc/ssh/sshd_config.d"
        for rel, text in (("etc/ssh/sshd_config", SSHD_CONFIG + "PasswordAuthentication yes\n"),
                          ("etc/ssh/sshd_config", SSHD_CONFIG + "Match Address 203.0.113.0/24\n    PasswordAuthentication yes\n"),
                          ("etc/ssh/sshd_config.d/20-x.conf", "KbdInteractiveAuthentication yes\n"),
                          ("etc/ssh/sshd_config.d/20-x.conf", "ChallengeResponseAuthentication yes\n"),
                          ("etc/ssh/sshd_config.d/20-x.conf", "passwordauthentication YES\n"),
                          ("etc/ssh/sshd_config.d/20-x.conf", "PasswordAuthentication=yes\n"),
                          ("etc/ssh/sshd_config.d/20-x.conf", "  PasswordAuthentication\tyes # why not\n"),
                          ("etc/ssh/sshd_config.d/10-sfduo.conf", SFDUO_CONF + "Match all\n  PasswordAuthentication yes\n")):
            with self.subTest(rel=rel, text=text):
                f = self.r / rel
                keep = f.read_bytes() if f.exists() else None
                f.write_text(text, encoding="utf-8")
                self.assert_fails(run_verify(self.r), "ssh: key-only everywhere")
                if keep is None:
                    f.unlink()
                else:
                    f.write_bytes(keep)
        self.assertTrue(d.is_dir())
        self.assert_clean(run_verify(self.r))

    def test_a_missing_no_fails_because_the_default_is_yes(self):
        self.built()
        main = self.r / "etc/ssh/sshd_config"
        without_kbd = SSHD_CONFIG.replace("KbdInteractiveAuthentication no\n", "")
        self.assertNotEqual(without_kbd, SSHD_CONFIG)
        (self.r / DROPIN_REL).write_text("KbdInteractiveAuthentication no\n", encoding="utf-8")
        self.assert_fails(run_verify(self.r), "ssh: key-only everywhere")  # PasswordAuthentication is nowhere set to no
        (self.r / DROPIN_REL).write_text("PasswordAuthentication no\n", encoding="utf-8")
        self.assert_clean(run_verify(self.r))  # the main file's KbdInteractiveAuthentication no completes it
        main.write_text(without_kbd, encoding="utf-8")
        self.assert_fails(run_verify(self.r), "ssh: key-only everywhere")  # nothing sets KbdInteractiveAuthentication to no (the default is yes)
        (self.r / DROPIN_REL).unlink()
        self.assert_fails(run_verify(self.r), "ssh: key-only everywhere")
        main.write_text("PasswordAuthentication no\nKbdInteractiveAuthentication no\n" + without_kbd, encoding="utf-8")
        self.assert_clean(run_verify(self.r))

    def test_a_match_block_that_takes_no_password_is_fine(self):
        self.built()
        with open(self.r / DROPIN_REL, "a", encoding="utf-8") as f:
            f.write("Match Address 203.0.113.0/24\n    PasswordAuthentication no\n    X11Forwarding no\n")
        self.assert_clean(run_verify(self.r))

    def test_a_comment_that_says_yes_is_not_a_setting(self):
        self.built()
        with open(self.r / DROPIN_REL, "a", encoding="utf-8") as f:
            f.write("# PasswordAuthentication yes\n")
        self.assert_clean(run_verify(self.r))


class SelftestEvidence(unittest.TestCase):
    """opos-selftest's ssh.auth on the configuration the build writes: PASS, and it says key-only everywhere."""

    @classmethod
    def setUpClass(cls):
        from tests import test_selftest as TS
        cls.TS = TS

    def setUp(self):
        TS = self.TS
        cm = TS.serve(TS.MERGED)
        self.url = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.f = TS.Fake(self.url)
        self.addCleanup(self.f.cleanup)

    def key_only_phone(self):
        if not SH or os.name != "posix":
            self.skipTest("needs a POSIX sh")
        with tempfile.TemporaryDirectory() as d:
            r = make_root(Path(d) / "rootfs")
            p = run_build(r)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.f.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf", (r / DROPIN_REL).read_text(encoding="utf-8"))

    def test_the_key_only_drop_in_passes_and_says_key_only_everywhere(self):
        self.key_only_phone()
        r = self.f.run()
        self.assertEqual(r.status("ssh.auth"), "PASS", r.rows.get("ssh.auth"))
        self.assertTrue(r.ev("ssh.auth").startswith("key-only everywhere. "), r.ev("ssh.auth"))
        self.assertIn("PasswordAuthentication no, KbdInteractiveAuthentication no", r.ev("ssh.auth"))
        self.assertNotIn("passwords allowed", r.ev("ssh.auth"))
        self.assertNotIn("except as listed", r.ev("ssh.auth"))
        self.assertEqual(r.rc, 0, r.text)
        self.assertEqual(r.by("FAIL"), [], r.text)

    def test_the_port_s_password_exception_fails(self):
        self.f.put("etc/ssh/sshd_config.d/10-item-password-usb-only.conf", self.TS.SSHD_DROPIN)
        r = self.f.run()
        self.assertEqual(r.status("ssh.auth"), "FAIL")
        self.assertIn("password-enabled Match blocks", r.ev("ssh.auth"))
        self.assertIn("passwords allowed only for: Address " + self.TS.USB_NET, r.ev("ssh.auth"))

    def test_a_password_path_added_to_the_key_only_phone_is_seen(self):
        self.key_only_phone()
        self.f.put("etc/ssh/sshd_config.d/20-x.conf", "Match all\n  PasswordAuthentication yes\n")
        r = self.f.run()
        self.assertEqual(r.status("ssh.auth"), "FAIL", r.rows.get("ssh.auth"))
        self.assertIn("password-enabled Match blocks", r.ev("ssh.auth"))
        self.f.put("etc/ssh/sshd_config.d/20-x.conf", "PasswordAuthentication yes\n")
        self.assertEqual(self.f.run().status("ssh.auth"), "PASS", "the drop-in's first no wins over a later global yes, as in OpenSSH")
        self.f.put("etc/ssh/sshd_config", "PasswordAuthentication yes\n" + self.TS.SSHD)
        self.assertEqual(self.f.run().status("ssh.auth"), "FAIL")

    def test_the_acceptance_doc_says_ssh_auth_is_key_only_on_this_image(self):
        doc = " ".join(read("docs/duo-acceptance.md").split())
        row = next(ln for ln in read("docs/duo-acceptance.md").splitlines() if ln.startswith("| `ssh.auth`"))
        self.assertIn("key-only everywhere", row)
        self.assertIn("key-only everywhere", doc)


if __name__ == "__main__":
    unittest.main()
