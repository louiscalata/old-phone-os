# SPDX-License-Identifier: GPL-3.0-or-later
"""The opt-in polkit rule that lets the OPOS server's user join Wi-Fi (OPOS_NET_POLKIT=1, packaging/duo-os/50-opos-network.rules).

On the owner's Surface Duo (checkpoint 1, November 8, 2026) joining Wi-Fi was refused: NetworkManager asks polkit for wifi.scan, network-control and
settings.modify.system, the image had no rule, and droidian cannot answer an administrator prompt. Read in the base rootfs (item-duo1-20261025-port0.22.1):
polkitd 128-1 (rules in /etc/polkit-1/rules.d and /usr/share/polkit-1/rules.d, run by duktape: polkitd links libduktape.so.207), network-manager 1.56.1, and the 17 actions of
/usr/share/polkit-1/actions/org.freedesktop.NetworkManager.policy listed below. The base's own rule, org.freedesktop.NetworkManager.rules, gives
settings.modify.system only to a local, ACTIVE session of the sudo or netdev group, which a system service has none of; its defaults for the other three
(allow_any) are "ask an administrator" for network-control and wifi.scan and "no" for enable-disable-wifi.

What is checked here: the rule's four ids are real actions of that policy and are exactly what src/opos/net.py needs; its JavaScript does what it says (run
under node where there is one); the build installs it only for OPOS_NET_POLKIT=1 and refuses a typo; verify-image.sh holds the image to it."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
PKG = ROOT / "packaging" / "duo-os"
RULE = PKG / "50-opos-network.rules"
SH = shutil.which("sh")
NODE = shutil.which("node")
NM = "org.freedesktop.NetworkManager."

from opos import net  # noqa: E402

# every <action id> of org.freedesktop.NetworkManager.policy in the base rootfs (network-manager 1.56.1-1~git20260528000904.6438c5a.next.wwan.fix)
BASE_POLICY = tuple(NM + a for a in (
    "enable-disable-network", "reload", "sleep-wake", "enable-disable-wifi", "enable-disable-wwan", "enable-disable-wimax", "network-control", "wifi.scan",
    "wifi.share.protected", "wifi.share.open", "settings.modify.own", "settings.modify.system", "settings.modify.hostname", "settings.modify.global-dns",
    "checkpoint-rollback", "enable-disable-statistics", "enable-disable-connectivity-check"))
FOUR = tuple(NM + a for a in ("enable-disable-wifi", "network-control", "settings.modify.system", "wifi.scan"))


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


CHECK_BLOCK = ("build-image.sh", "# --- polkit: CHECK BEGIN", "# --- polkit: CHECK END")
NET_BLOCK = ("build-image.sh", "# --- track: network", "# --- end track: network ---")
VERIFY_BLOCK = ("verify-image.sh", "# --- polkit: BEGIN", "# --- polkit: END")
EXPECT_BLOCK = ("verify-image.sh", "# --- expect: CHECK BEGIN", "# --- expect: CHECK END")


class TheRule(unittest.TestCase):
    def ids(self):
        return set(re.findall(r'"(org\.freedesktop\.NetworkManager\.[A-Za-z.-]+)"', RULE.read_text(encoding="utf-8")))

    def test_the_four_ids_are_real_actions_of_the_bases_policy(self):
        self.assertEqual(len(BASE_POLICY), 17)
        self.assertEqual(self.ids(), set(FOUR))
        self.assertLessEqual(self.ids(), set(BASE_POLICY))

    def test_they_are_exactly_what_the_network_page_asks_for(self):
        self.assertEqual(self.ids(), set(net.ACTIONS))
        needs = {NM + p for v in net.NEEDS.values() for p in v}
        self.assertEqual(needs, set(FOUR))
        for change, perms in net.NEEDS.items():  # scan, join, connect, forget, disconnect, power: each one's permissions are all in the rule
            for p in perms:
                self.assertIn(NM + p, self.ids(), change)

    def test_nothing_else_is_granted(self):
        for extra in set(BASE_POLICY) - set(FOUR):
            self.assertNotIn(extra, RULE.read_text(encoding="utf-8"))
        text = RULE.read_text(encoding="utf-8")
        self.assertNotIn("wifi.share", text)
        self.assertNotRegex(text, r"NetworkManager\.\*|indexOf\(\"org\.freedesktop\.NetworkManager\.\"\)|startsWith")

    def test_it_is_one_rule_for_one_user_that_says_yes(self):
        text = RULE.read_text(encoding="utf-8")
        self.assertEqual(text.count("polkit.addRule"), 1)
        self.assertEqual(text.count("polkit.Result.YES"), 1)
        self.assertNotIn("AUTH_", text)
        self.assertIn('subject.user == "droidian"', text)
        self.assertNotRegex(text, r"isInGroup|subject\.local|subject\.active")  # no session: a system service has none, and a group would widen it

    def test_it_uses_only_what_every_polkit_javascript_engine_runs(self):
        # polkitd 128 in the base runs rules in duktape (libduktape.so.207; a polkit built for mozjs would also run them): plain ES5 only
        code = "\n".join(ln for ln in RULE.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("//"))
        for banned in ("=>", "`", "let ", "const ", "class ", "async ", "?.", "??", "...", "import ", "require("):
            self.assertNotIn(banned, code)
        self.assertEqual(code.count("{"), code.count("}"))
        self.assertEqual(code.count("("), code.count(")"))
        self.assertEqual(code.count("["), code.count("]"))

    def test_it_has_no_carriage_returns_and_says_who_it_is_for(self):
        raw = RULE.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.startswith(b"// SPDX-License-Identifier: GPL-3.0-or-later\n"))
        self.assertIn(b"opos-server.service", raw)

    @unittest.skipUnless(NODE, "needs node to run the rule's JavaScript")
    def test_the_javascript_grants_the_four_only_to_the_droidian_server_unit(self):
        js = ("const fs = require('fs'); const rules = [];\n"
              "const polkit = { addRule: f => rules.push(f), Result: { YES: 'yes', NO: 'no', AUTH_ADMIN: 'auth_admin', NOT_HANDLED: null } };\n"
              "new Function('polkit', fs.readFileSync(process.argv[1], 'utf8'))(polkit);\n"
              "const cases = JSON.parse(process.argv[2]);\n"
              "console.log(JSON.stringify([rules.length, cases.map(([id, user, system_unit]) => { let r = null; for (const f of rules) { const v = f({id: id}, {user: user, system_unit: system_unit});"
              " if (v !== undefined && v !== null) { r = v; break; } } return r; })]));\n")
        cases = [[a, "droidian", "opos-server.service"] for a in FOUR]
        cases += [[a, "droidian", "opos-server.service"] for a in set(BASE_POLICY) - set(FOUR)]
        cases += [[a, u, "opos-server.service"] for a in FOUR for u in ("root", "Droidian", "droidian2", "", "polkitd", "nobody", "droidian ")]
        cases += [[a, "droidian", "opos-server.service"] for a in ("org.freedesktop.login1.reboot", "org.freedesktop.packagekit.package-install", NM, NM + "wifi.scan.x",
                                           "x" + NM + "wifi.scan", NM + "wifi.", "")]
        cases += [[a, "droidian", unit] for a in FOUR for unit in (None, "", "sshd.service", "opos.service")]
        p = subprocess.run([NODE, "-e", js, str(RULE), json.dumps(cases)], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        n_rules, got = json.loads(p.stdout)
        self.assertEqual(n_rules, 1)
        want = ["yes" if (a in FOUR and u == "droidian" and unit == "opos-server.service") else None for a, u, unit in cases]
        bad = [(c, g) for c, g, w in zip(cases, got, want) if g != w]
        self.assertEqual(bad, [])
        self.assertEqual(got.count("yes"), 4)


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class BuildBlocks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.r = self.tmp / "rootfs"
        self.rules = self.r / "etc/polkit-1/rules.d"
        self.rules.mkdir(parents=True)
        self.rules.chmod(0o750)  # as the base has it: root and polkitd's group only

    def run_block(self, spec, polkit=None, setup="", after=""):
        script = self.tmp / "run.sh"
        script.write_text("set -eu\nr=%s\nhere=%s\n%s%s\necho BLOCK-DONE\n" % (self.r, PKG, setup, block(*spec) + after))
        env = {k: v for k, v in os.environ.items() if k != "OPOS_NET_POLKIT"}
        if polkit is not None:
            env["OPOS_NET_POLKIT"] = polkit
        return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=60)

    def test_the_check_accepts_unset_empty_zero_and_one(self):
        for v in (None, "", "0", "1"):
            with self.subTest(v=v):
                p = self.run_block(CHECK_BLOCK, v)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("BLOCK-DONE", p.stdout)

    def test_the_check_refuses_a_typo_before_anything_is_built(self):
        for v in ("yes", "true", "on", "2", "1 ", " 1", "01", "y", "-1"):
            with self.subTest(v=v):
                p = self.run_block(CHECK_BLOCK, v)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertNotIn("BLOCK-DONE", p.stdout)
                self.assertIn("OPOS_NET_POLKIT must be 1", p.stderr)

    def test_the_check_comes_before_the_output_is_touched(self):
        t = read("packaging/duo-os/build-image.sh")
        self.assertLess(t.index("# --- polkit: CHECK END"), t.index('cp --sparse=always "$base" "$image"'))
        self.assertLess(t.index("# --- polkit: CHECK END"), t.index("mount -o loop"))

    def test_the_whole_script_refuses_a_typo_without_copying(self):
        d = self.tmp / "bin"
        d.mkdir()
        for n in ("mktemp", "dirname", "readlink", "cp", "rm", "rmdir", "umount", "env"):
            w = shutil.which(n)
            if not w:
                self.skipTest("needs " + n)
            (d / n).symlink_to(w)
        (d / "id").write_text("#!/bin/sh\necho 0\n")
        (d / "id").chmod(0o755)
        base, out = self.tmp / "base.img", self.tmp / "out.img"
        base.write_bytes(b"x")
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPOS_")}
        env.update(PATH=str(d), OPOS_NET_POLKIT="yes")
        p = subprocess.run([SH, str(PKG / "build-image.sh"), str(base), str(out)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("OPOS_NET_POLKIT must be 1", p.stderr)
        self.assertFalse(out.exists())

    def test_with_the_knob_the_rule_is_installed_as_the_packaging_file_and_the_directory_is_left_alone(self):
        p = self.run_block(NET_BLOCK, "1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        f = self.rules / "50-opos-network.rules"
        self.assertTrue(f.is_file() and not f.is_symlink())
        self.assertEqual(f.read_bytes(), RULE.read_bytes())
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o644")
        self.assertEqual(oct(self.rules.stat().st_mode & 0o777), "0o750", "rules.d keeps the mode that lets polkitd in by its group")
        self.assertIn("NETWORK: polkit rule installed", p.stdout)
        self.assertIn("etc/polkit-1/rules.d/50-opos-network.rules", p.stdout, "the manifest line with its hash")
        self.assertTrue((self.r / "etc/systemd/system/opos-server.service.d/20-opos-network.conf").is_file())

    def test_the_later_modem_rule_install_also_preserves_the_bases_directory_mode(self):
        text = read("packaging/duo-os/build-image.sh")
        modem = text.split("# The Duo server user is droidian.", 1)[1].split("# --- recovery: BEGIN", 1)[0]
        modem = modem.split("\n", 1)[1]
        p = self.run_block(NET_BLOCK, "1", after=modem)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue((self.rules / "50-opos-modem.rules").is_file())
        self.assertEqual(self.rules.stat().st_mode & 0o777, 0o750)

    def test_without_the_knob_there_is_no_rule_and_a_stale_one_goes(self):
        for v in (None, "", "0"):
            with self.subTest(v=v):
                f = self.rules / "50-opos-network.rules"
                f.write_text("// an older image's rule\n", encoding="utf-8")
                p = self.run_block(NET_BLOCK, v)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertFalse(f.exists() or f.is_symlink())
                self.assertNotIn("polkit rule installed", p.stdout)
                self.assertTrue((self.r / "etc/systemd/system/opos-server.service.d/20-opos-network.conf").is_file(), "the drop-in is always installed")

    def test_the_static_shape_the_older_network_test_pins_is_unchanged(self):
        blk = block(*NET_BLOCK)
        self.assertRegex(blk, r'if \[ "\$\{OPOS_NET_POLKIT:-0\}" = 1 \]')
        self.assertLess(blk.index("OPOS_NET_POLKIT"), blk.index("50-opos-network.rules"))


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class VerifyBlock(unittest.TestCase):
    """The verify-image.sh block against a scratch root file system. A stat function answers the rule's owner and the directory's group, which an
    unprivileged test cannot set."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.r = self.tmp / "rootfs"
        self.d = self.r / "etc/polkit-1/rules.d"
        self.d.mkdir(parents=True)
        self.d.chmod(0o750)
        (self.r / "etc/group").write_text("root:x:0:\npolkitd:x:983:\n", encoding="utf-8")
        self.rule = self.d / "50-opos-network.rules"

    def with_rule(self, body=None, mode=0o644):
        self.rule.write_bytes(RULE.read_bytes() if body is None else body.encode())
        self.rule.chmod(mode)

    def run_verify(self, owner="0:0", dirgid="983", expect=None):
        script = self.tmp / "verify.sh"
        script.write_text('fail=0; r=%s; here=%s\n'
                          'check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n'
                          'stat() { case "$1 $2" in "-c %%u:%%g") echo %s;; "-c %%g") echo %s;; *) command stat "$@";; esac; }\n'
                          '%s\nexit $fail\n' % (self.r, PKG, owner, dirgid, block(*VERIFY_BLOCK)))
        env = {k: v for k, v in os.environ.items() if k != "OPOS_NET_POLKIT"}
        if expect is not None:
            env["OPOS_NET_POLKIT"] = expect
        return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=60)

    def assert_clean(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("FAIL", p.stdout)

    def test_the_shipped_rule_in_a_readable_directory_passes(self):
        self.with_rule()
        p = self.run_verify()
        self.assert_clean(p)
        self.assertIn("PASS polkit: 50-opos-network.rules is a plain root-owned 0644 file with the very bytes", p.stdout)
        self.assertIn("PASS polkit: /etc/polkit-1/rules.d can be entered by polkitd", p.stdout)
        self.assertIn("NOTE: polkit rule installed", p.stdout)

    def test_an_image_without_the_rule_passes_with_a_note_and_nothing_required(self):
        p = self.run_verify()
        self.assert_clean(p)
        self.assertIn("NOTE: no polkit rule from OPOS", p.stdout)
        self.assertNotIn("PASS", p.stdout)

    def test_a_rule_that_differs_by_a_byte_fails(self):
        text = RULE.read_text(encoding="utf-8")
        for body in (text.replace('"org.freedesktop.NetworkManager.wifi.scan"', '"org.freedesktop.NetworkManager.wifi.scan",\n        "org.freedesktop.NetworkManager.settings.modify.hostname"'),
                     text.replace('        "org.freedesktop.NetworkManager.wifi.scan"\n', ""), text.replace("droidian", "root"), text.replace("YES", "AUTH_ADMIN"),
                     text.replace("\n", "\r\n"), text + "\n", text.rstrip("\n"), "", text.replace("subject.user == ", "subject.user != ")):
            with self.subTest(body=body[-60:]):
                self.with_rule(body)
                p = self.run_verify()
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("FAIL polkit: 50-opos-network.rules is a plain", p.stdout)

    def test_the_wrong_mode_owner_or_a_link_fails(self):
        for mode in (0o664, 0o600, 0o755, 0o640, 0o666):
            with self.subTest(mode=oct(mode)):
                self.with_rule(mode=mode)
                p = self.run_verify()
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("FAIL polkit: 50-opos-network.rules is a plain", p.stdout)
        self.with_rule()
        for owner in ("1000:1000", "0:1000", "1000:0", ""):
            with self.subTest(owner=owner):
                p = self.run_verify(owner=owner or "''")
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.rule.unlink()
        target = self.tmp / "t.rules"
        target.write_bytes(RULE.read_bytes())
        target.chmod(0o644)
        self.rule.symlink_to(target)
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL polkit: 50-opos-network.rules is a plain", p.stdout)

    def test_a_directory_polkitd_cannot_enter_fails(self):
        self.with_rule()
        for mode, gid, ok in ((0o750, "983", True), (0o755, "1000", True), (0o770, "983", True), (0o750, "1000", False), (0o700, "983", False),
                              (0o740, "983", False), (0o710, "983", False), (0o751, "1000", False), (0o2750, "983", True)):
            with self.subTest(mode=oct(mode), gid=gid):
                self.d.chmod(mode)
                p = self.run_verify(dirgid=gid)
                if ok:
                    self.assert_clean(p)
                else:
                    self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                    self.assertIn("FAIL polkit: /etc/polkit-1/rules.d can be entered by polkitd", p.stdout)
                    self.assertIn("PASS polkit: 50-opos-network.rules is a plain", p.stdout)

    def test_an_image_whose_group_file_has_no_polkitd_is_entered_by_everyone_or_not_at_all(self):
        self.with_rule()
        (self.r / "etc/group").write_text("root:x:0:\n", encoding="utf-8")
        self.assertEqual(self.run_verify().returncode, 1)
        self.d.chmod(0o755)
        self.assert_clean(self.run_verify())

    def test_the_variable_from_the_build_is_held_both_ways(self):
        self.with_rule()
        p = self.run_verify(expect="1")
        self.assert_clean(p)
        self.assertIn("PASS OPOS_NET_POLKIT=1: the polkit rule is in the image", p.stdout)
        p = self.run_verify(expect="0")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL OPOS_NET_POLKIT=0: no polkit rule from OPOS is in the image", p.stdout)
        self.rule.unlink()
        p = self.run_verify(expect="0")
        self.assert_clean(p)
        self.assertIn("PASS OPOS_NET_POLKIT=0", p.stdout)
        p = self.run_verify(expect="1")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL OPOS_NET_POLKIT=1: the polkit rule is in the image", p.stdout)

    def test_a_dangling_link_counts_as_present_for_the_variable(self):
        self.rule.symlink_to(self.tmp / "nowhere")
        p = self.run_verify(expect="0")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL OPOS_NET_POLKIT=0", p.stdout)

    def test_without_the_variable_it_is_not_asked_about(self):
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    self.with_rule()
                p = self.run_verify()
                self.assert_clean(p)
                self.assertNotIn("OPOS_NET_POLKIT=", p.stdout)


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class VerifyExpectCheck(unittest.TestCase):
    def run_block(self, polkit=None):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "run.sh"
            script.write_text("set -u\n%s\necho BLOCK-DONE\n" % block(*EXPECT_BLOCK))
            env = {k: v for k, v in os.environ.items() if k not in ("OPOS_NET_POLKIT", "OPOS_DEV_SUDO")}
            if polkit is not None:
                env["OPOS_NET_POLKIT"] = polkit
            return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=60)

    def test_unset_empty_zero_and_one_are_accepted(self):
        for v in (None, "", "0", "1"):
            with self.subTest(v=v):
                p = self.run_block(v)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("BLOCK-DONE", p.stdout)

    def test_anything_else_is_refused_before_the_image_is_touched(self):
        for v in ("yes", "true", "2", "1 ", "on"):
            with self.subTest(v=v):
                p = self.run_block(v)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertNotIn("BLOCK-DONE", p.stdout)
                self.assertIn("OPOS_NET_POLKIT must be 1, 0 or unset", p.stderr)

    def test_verify_knows_where_the_shipped_rule_is(self):
        t = read("packaging/duo-os/verify-image.sh")
        self.assertRegex(t, r'(?m)^here=\$\(cd "\$\(dirname "\$0"\)" && pwd\)$')
        self.assertLess(t.index("here=$(cd"), t.index("# --- polkit: BEGIN"))
        self.assertLess(t.index("# --- expect: CHECK END"), t.index("mount -o ro,loop,noload"))
        self.assertIn("$here/50-opos-network.rules", block(*VERIFY_BLOCK))


class Docs(unittest.TestCase):
    def test_the_docs_say_what_was_read_and_what_the_build_and_verifier_do(self):
        net_md = " ".join(read("docs/network.md").split())
        for phrase in ("polkitd 128", "network-control", "wifi.scan", "settings.modify.system", "enable-disable-wifi", "org.freedesktop.NetworkManager.rules",
                       "stops the build before it starts", "verify-image.sh", "libduktape.so.207"):
            self.assertIn(phrase, net_md)
        readme = " ".join(read("packaging/duo-os/README.md").split())
        self.assertIn("OPOS_NET_POLKIT", readme)
        self.assertIn("sudo env OPOS_DEV_SUDO=1 OPOS_NET_POLKIT=1", readme)
        self.assertIn("OPOS_NET_POLKIT", read("CHANGELOG.md"))


if __name__ == "__main__":
    unittest.main()
