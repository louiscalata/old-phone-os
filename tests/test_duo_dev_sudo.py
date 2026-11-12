# SPDX-License-Identifier: GPL-3.0-or-later
"""OPOS_DEV_SUDO: a dev image where the login user, droidian, has passwordless sudo.

In the base, droidian is in the sudo group but sudo asks for a password nobody has, and root cannot log in, so a developer who can ssh in cannot become
root. `build-image.sh` therefore has a dev-only knob, `OPOS_DEV_SUDO=1`, that works only together with `OPOS_DEV_SSH_PUBKEY` and writes
/etc/sudoers.d/90-opos-dev (0440 root:root, exactly `droidian ALL=(ALL) NOPASSWD:ALL`). `verify-image.sh` checks the file both ways, and
`opos-selftest` WARNs (`dev.sudo`) when it is there. The blocks of both scripts are run here against a scratch root file system, the way the dev-span,
clock and OTA blocks are; no image, mount or qemu is involved. Nothing here installs or runs sudo."""
import os
import base64
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
LINE = "droidian ALL=(ALL) NOPASSWD:ALL"
TEST_KEY = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + bytes(range(32))).decode() + " fixture\n"
FILE = "etc/sudoers.d/90-opos-dev"
PASSWD = "root:x:0:0:root:/root:/bin/bash\ndroidian:x:32011:32011:droidian:/home/droidian:/bin/bash\n"

from tests import test_selftest as TS  # noqa: E402


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


CHECK_BLOCK = ("build-image.sh", "# --- dev sudo: CHECK BEGIN", "# --- dev sudo: CHECK END")
WRITE_BLOCK = ("build-image.sh", "# --- dev sudo: BEGIN", "# --- dev sudo: END")
VERIFY_BLOCK = ("verify-image.sh", "# --- dev sudo: BEGIN", "# --- dev sudo: END")
EXPECT_BLOCK = ("verify-image.sh", "# --- expect: CHECK BEGIN", "# --- expect: CHECK END")


def tool_dir(parent, names, fakes=None):
    """A directory of symlinks to the named tools (and of the given fake scripts), to use as the whole PATH."""
    d = Path(parent)
    d.mkdir(exist_ok=True)
    for n in names:
        w = shutil.which(n)
        if w is None:
            raise unittest.SkipTest("needs " + n)
        if not (d / n).exists():
            (d / n).symlink_to(w)
    for n, body in (fakes or {}).items():
        (d / n).write_text(body, encoding="utf-8")
        (d / n).chmod(0o755)
    return str(d)


class StaticPins(unittest.TestCase):
    def test_each_block_is_delimited_once(self):
        for spec in (CHECK_BLOCK, WRITE_BLOCK, VERIFY_BLOCK, EXPECT_BLOCK):
            with self.subTest(spec=spec):
                self.assertTrue(block(*spec).strip())

    def test_the_check_runs_before_the_output_is_touched_and_the_writer_sits_after_the_dev_key_and_the_span_and_before_the_updater(self):
        t = read("packaging/duo-os/build-image.sh")
        self.assertLess(t.index("# --- dev sudo: CHECK END"), t.index('cp --sparse=always "$base" "$image"'))
        self.assertLess(t.index("# --- dev sudo: CHECK END"), t.index("mount -o loop"))
        self.assertLess(t.index("OPOS_KIOSK_DEBUG_PORT=9222"), t.index("# --- dev sudo: BEGIN"))
        self.assertLess(t.index("# --- dev span: END"), t.index("# --- dev sudo: BEGIN"))
        self.assertLess(t.index("# --- dev sudo: END"), t.index("# ---- BEGIN OPOS OTA updates"))

    def test_the_verify_block_runs_after_the_droidian_home_is_known_and_the_expectation_check_before_the_image_is_mounted(self):
        t = read("packaging/duo-os/verify-image.sh")
        self.assertLess(t.index("chome=$(awk"), t.index("# --- dev sudo: BEGIN"))
        self.assertLess(t.index("# --- expect: CHECK END"), t.index("mount -o ro,loop,noload"))

    def test_the_one_line_and_the_one_file_name_are_the_same_everywhere(self):
        for path in ("packaging/duo-os/build-image.sh", "packaging/duo-os/verify-image.sh", "packaging/duo-os/README.md"):
            self.assertIn(LINE, read(path), path)
        for path in ("packaging/duo-os/build-image.sh", "packaging/duo-os/verify-image.sh", "packaging/duo-os/opos-selftest", "packaging/duo-os/README.md",
                     "docs/duo-os-final.md"):
            self.assertIn("90-opos-dev", read(path), path)
        self.assertRegex(read("packaging/duo-os/build-image.sh"), r'(?m)^rm -f "\$r/etc/sudoers\.d/90-opos-dev"$')

    def test_the_writer_installs_mode_0440_after_validating_and_prints_the_dev_line(self):
        code = "\n".join(ln for ln in block(*WRITE_BLOCK).splitlines() if not ln.lstrip().startswith("#"))
        self.assertIn('install -m 0440 "$sudo_tmp" "$r/etc/sudoers.d/90-opos-dev"', code)
        self.assertLess(code.index("visudo -cf"), code.index("install -m 0440"))
        self.assertIn("DEV: passwordless sudo for droidian", code)
        self.assertEqual(code.count("NOPASSWD"), 1, "the sudoers line is written in one place")

    def test_the_knob_is_documented_where_the_other_switches_are(self):
        readme = read("packaging/duo-os/README.md")
        self.assertRegex(readme, r"(?m)^\| `OPOS_DEV_SUDO` \| build input, dev images only \|")
        self.assertIn("`OPOS_DEV_SUDO=1`", readme)
        self.assertIn("`OPOS_DEV_SSH_PUBKEY`", readme)
        final = read("docs/duo-os-final.md")
        self.assertRegex(final, r"(?m)^\| `OPOS_DEV_SUDO` \|")
        self.assertIn("OPOS_DEV_SUDO", read("CHANGELOG.md"))
        for path, text in (("README", readme), ("final doc", final)):
            flat = " ".join(text.split())
            self.assertIn("must never be carried or shared", flat, path)

    def test_the_readme_has_a_section_that_says_what_the_knob_gives_away(self):
        readme = read("packaging/duo-os/README.md")
        self.assertIn("## Dev options: passwordless sudo", readme)
        sec = " ".join(readme[readme.index("## Dev options: passwordless sudo"):].split("\n## ")[0].split())
        for phrase in ("must never be carried or shared", "root", "OPOS server", "Chromium", "sudo rm /etc/sudoers.d/90-opos-dev", "visudo -cf",
                       "OPOS_DEV_SUDO=1", "exactly the one line", LINE):
            self.assertIn(phrase, sec)

    def test_the_acceptance_doc_lists_the_dev_sudo_warning_with_the_other_dev_warnings(self):
        doc = read("docs/duo-acceptance.md")
        row = next(ln for ln in doc.splitlines() if ln.startswith("| `dev.ssh-key`"))
        self.assertIn("`dev.sudo`", row.split("|")[1], "named among the checks in the first cell")
        self.assertIn("WARN", row)
        self.assertIn("passwordless sudo", row)
        self.assertIn("dev.sudo", read("docs/duo-os-final.md"))

    def test_the_scripts_parse_and_have_no_carriage_returns(self):
        for n in ("build-image.sh", "verify-image.sh"):
            raw = (PKG / n).read_bytes()
            self.assertNotIn(b"\r", raw, n)
            if SH:
                r = subprocess.run([SH, "-n", str(PKG / n)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, n + ": " + r.stderr)

    def test_the_verify_block_starts_no_server_and_runs_no_sudo(self):
        code = "\n".join(ln for ln in block(*VERIFY_BLOCK).splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("serve ", code)
        self.assertNotRegex(code, r"(?m)(^|[;&|(`])\s*sudo\s")  # no command line starts with sudo

    def test_the_selftest_cannot_run_sudo(self):
        t = read("packaging/duo-os/opos-selftest")
        self.assertNotIn('"sudo"', t)
        self.assertNotRegex(t, r"subprocess\.\w+\(\[?['\"]sudo")

    def test_release_builder_and_updater_reject_both_dev_only_files(self):
        import importlib.machinery
        import importlib.util
        from unittest import mock
        from tests.test_opos_release import rel

        loader = importlib.machinery.SourceFileLoader("opos_dev_boundary_updater", str(PKG / "updater/opos-updater"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        updater = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = updater
        self.addCleanup(sys.modules.pop, spec.name, None)
        # no __pycache__ beside the updater: tests.test_duo_updater reads every entry of that folder as a file
        keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            loader.exec_module(updater)
        finally:
            sys.dont_write_bytecode = keep
        with mock.patch.dict(os.environ, OPOS_DEV_SUDO="1", OPOS_DEV_SPAN="1", OPOS_DEV_SSH_PUBKEY="/dev/null"):
            for path in (FILE, "etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf"):
                with self.subTest(path=path):
                    with self.assertRaises(rel.Refused):
                        rel.classify(path)
                    with self.assertRaises(updater.Reject):
                        updater.classify(path)

    def test_release_image_recipes_override_inherited_dev_knobs(self):
        for path in ("tools/opos-release.py", "docs/ota-release-howto.md"):
            with self.subTest(path=path):
                commands = [ln for ln in read(path).splitlines() if "sh packaging/duo-os/build-image.sh" in ln]
                self.assertTrue(commands, path)
                for command in commands:
                    self.assertIn("OPOS_DEV_SPAN=0", command)
                    self.assertIn("OPOS_DEV_SUDO=0", command)

    def test_the_sudoers_line_is_valid_sudoers_syntax(self):
        # always checked by shape; checked by visudo as well where the machine has it (visudo -cf does not look at the file's owner or mode)
        self.assertRegex(LINE, r"^[a-z_][a-z0-9_-]* ALL=\(ALL\) NOPASSWD:ALL$")
        visudo = shutil.which("visudo")
        if not visudo:
            self.skipTest("no visudo on this machine")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "ok"
            f.write_text(LINE + "\n", encoding="utf-8")
            f.chmod(0o440)
            p = subprocess.run([visudo, "-cf", str(f)], capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            bad = Path(d) / "bad"
            bad.write_text("droidian ALL=(ALL) NOPASSWD ALL\n", encoding="utf-8")
            bad.chmod(0o440)
            self.assertNotEqual(subprocess.run([visudo, "-cf", str(bad)], capture_output=True, text=True, timeout=60).returncode, 0)


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class BuildCheck(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def env(self, sudo=None, key=None, **more):
        env = {k: v for k, v in os.environ.items() if k not in ("OPOS_DEV_SUDO", "OPOS_DEV_SSH_PUBKEY", "OPOS_DEV_SPAN")}
        if sudo is not None:
            env["OPOS_DEV_SUDO"] = sudo
        if key is not None:
            env["OPOS_DEV_SSH_PUBKEY"] = key
        env.update(more)
        return env

    def run_block(self, sudo=None, key=None):
        script = self.tmp / "run.sh"
        script.write_text("set -eu\n%s\necho BLOCK-DONE\n" % block(*CHECK_BLOCK))
        return subprocess.run([SH, str(script)], env=self.env(sudo, key), capture_output=True, text=True, timeout=60)

    def test_the_check_accepts_unset_empty_zero_and_one_with_a_key(self):
        for sudo, key in ((None, None), ("", None), ("0", None), (None, "/dev/null"), ("0", "/dev/null"), ("1", "/dev/null")):
            with self.subTest(sudo=sudo, key=key):
                p = self.run_block(sudo, key)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("BLOCK-DONE", p.stdout)

    def test_the_check_refuses_the_knob_without_the_dev_key(self):
        for key in (None, ""):
            with self.subTest(key=key):
                p = self.run_block("1", key)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertNotIn("BLOCK-DONE", p.stdout)
                self.assertIn("OPOS_DEV_SUDO=1 is a dev-image option", p.stderr)
                self.assertIn("OPOS_DEV_SSH_PUBKEY", p.stderr)

    def test_the_check_refuses_other_values_even_with_the_key(self):
        for sudo in ("2", "yes", "true", "on", "1 ", " 1", "01", "ALL", "NOPASSWD:ALL", "-1"):
            with self.subTest(sudo=sudo):
                p = self.run_block(sudo, "/dev/null")
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertNotIn("BLOCK-DONE", p.stdout)
                self.assertIn("OPOS_DEV_SUDO must be 1", p.stderr)

    def test_the_whole_script_refuses_before_it_copies_anything(self):
        # build-image.sh insists on root first; a stand-in `id` lets the unprivileged run reach the check, which comes before the first write
        fake = tool_dir(self.tmp / "bin", ["mktemp", "dirname", "readlink", "cp", "rm", "rmdir", "umount", "env"], {"id": "#!/bin/sh\necho 0\n"})
        base, out = self.tmp / "base.img", self.tmp / "out.img"
        base.write_bytes(b"not an image")
        for sudo, key, want in (("1", None, "OPOS_DEV_SUDO=1 is a dev-image option"), ("yes", "/dev/null", "OPOS_DEV_SUDO must be 1")):
            with self.subTest(sudo=sudo, key=key):
                p = subprocess.run([SH, str(PKG / "build-image.sh"), str(base), str(out)], env=self.env(sudo, key, PATH=fake), capture_output=True, text=True,
                                   timeout=60)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn(want, p.stderr)
                self.assertFalse(out.exists(), "OUT must not be touched")
                self.assertNotIn("copying", p.stdout)
        # An empty developer key is refused before the copy too.
        p = subprocess.run([SH, str(PKG / "build-image.sh"), str(base), str(out)], env=self.env("1", "/dev/null", PATH=fake), capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertNotIn("copying", p.stdout)


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class BuildWrite(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.r = self.tmp / "rootfs"
        (self.r / "etc/sudoers.d").mkdir(parents=True)
        (self.r / "etc/passwd").write_text(PASSWD, encoding="utf-8")
        (self.r / "etc/sudoers").write_text("%sudo ALL=(ALL:ALL) ALL\n@includedir /etc/sudoers.d\n")
        self.log = self.tmp / "visudo.log"

    def run_block(self, sudo=None, key=None, path=None, **more):
        script = self.tmp / "run.sh"
        script.write_text("set -eu\nr=%s\nhere=%s\n%secho BLOCK-DONE\n" % (self.r, PKG, block(*WRITE_BLOCK)))
        env = {k: v for k, v in os.environ.items() if k not in ("OPOS_DEV_SUDO", "OPOS_DEV_SSH_PUBKEY")}
        if sudo is not None:
            env["OPOS_DEV_SUDO"] = sudo
        if key is not None:
            env["OPOS_DEV_SSH_PUBKEY"] = key
        if path is not None:
            env["PATH"] = path
        env.update(more)
        return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=60)

    def fake_visudo(self, rc=0):
        body = ('#!/bin/sh\necho "visudo $*" >> "$LOG"\nfor a; do f=$a; done\n'
                '{ echo "mode=$(stat -c %%a "$f")"; cat "$f"; } >> "$LOG"\nexit %d\n' % rc)
        return tool_dir(self.tmp / "fakebin", ["awk", "mktemp", "chmod", "install", "rm", "sha256sum", "stat", "cat"], {"visudo": body})

    def test_a_dev_image_gets_exactly_the_sudoers_file(self):
        p = self.run_block("1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        f = self.r / FILE
        self.assertTrue(f.is_file() and not f.is_symlink())
        self.assertEqual(f.read_bytes(), (LINE + "\n").encode())
        self.assertEqual(oct(statmod.S_IMODE(f.stat().st_mode)), "0o440")
        self.assertIn("DEV: passwordless sudo for droidian", p.stdout)
        self.assertIn("must never be carried or shared", p.stdout)
        self.assertIn(FILE, p.stdout, "the manifest line with the file's hash")

    def test_the_file_and_its_directory_are_the_only_things_written(self):
        before = {str(x.relative_to(self.r)) for x in self.r.rglob("*")}
        p = self.run_block("1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        after = {str(x.relative_to(self.r)) for x in self.r.rglob("*")}
        self.assertEqual(after - before, {FILE})
        self.assertEqual((self.r / "etc/passwd").read_text(encoding="utf-8"), PASSWD)

    def test_the_directory_is_made_when_the_base_lacks_it(self):
        shutil.rmtree(self.r / "etc/sudoers.d")
        p = self.run_block("1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.r / FILE).read_bytes(), (LINE + "\n").encode())

    def test_no_file_without_the_knob_or_without_the_key(self):
        for sudo, key in ((None, None), (None, "/dev/null"), ("", "/dev/null"), ("0", "/dev/null"), ("1", None), ("1", "")):
            with self.subTest(sudo=sudo, key=key):
                p = self.run_block(sudo, key)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertFalse((self.r / FILE).exists())
                self.assertNotIn("DEV:", p.stdout)
                self.assertNotIn("passwordless", p.stdout)

    def test_a_stale_file_from_an_earlier_dev_image_is_removed_and_a_new_one_replaces_it(self):
        stale = self.r / FILE
        stale.write_text("droidian ALL=(ALL) NOPASSWD:ALL\nroot ALL=(ALL) NOPASSWD:ALL\n", encoding="utf-8")
        p = self.run_block(None, "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(stale.exists() or stale.is_symlink())
        stale.write_text("old\n", encoding="utf-8")
        stale.chmod(0o600)
        p = self.run_block("1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(stale.read_bytes(), (LINE + "\n").encode())
        self.assertEqual(oct(statmod.S_IMODE(stale.stat().st_mode)), "0o440")

    def test_a_symlink_in_the_way_is_replaced_not_followed(self):
        target = self.tmp / "elsewhere"
        target.write_text("keep\n", encoding="utf-8")
        link = self.r / FILE
        link.symlink_to(target)
        p = self.run_block("1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(link.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")

    def test_a_base_without_the_droidian_user_stops_the_build(self):
        (self.r / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
        p = self.run_block("1", "/dev/null")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("no droidian user", p.stderr)
        self.assertFalse((self.r / FILE).exists())

    def test_visudo_is_asked_to_check_the_exact_line_in_a_0440_file_before_anything_is_installed(self):
        p = self.run_block("1", "/dev/null", path=self.fake_visudo() + os.pathsep + os.environ["PATH"], LOG=str(self.log))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3, lines)
        self.assertRegex(lines[0], r"^visudo -cf /\S+$")
        self.assertEqual(lines[1], "mode=440")
        self.assertEqual(lines[2], LINE)
        self.assertEqual((self.r / FILE).read_bytes(), (LINE + "\n").encode())

    def test_a_visudo_that_rejects_the_line_stops_the_build_and_leaves_no_file(self):
        stale = self.r / FILE
        stale.write_text("old\n", encoding="utf-8")
        p = self.run_block("1", "/dev/null", path=self.fake_visudo(rc=1) + os.pathsep + os.environ["PATH"], LOG=str(self.log))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("visudo rejects", p.stderr)
        self.assertNotIn("BLOCK-DONE", p.stdout)
        self.assertNotIn("DEV: passwordless sudo", p.stdout)
        self.assertFalse((self.r / FILE).exists(), "a line visudo rejects must not reach the image (the stale file goes too)")

    def test_a_builder_without_visudo_still_builds_the_file(self):
        only = tool_dir(self.tmp / "novisudo", ["awk", "mktemp", "chmod", "install", "rm", "sha256sum", "python3"])
        p = self.run_block("1", "/dev/null", path=only)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.r / FILE).read_bytes(), (LINE + "\n").encode())

    def test_the_real_visudo_accepts_what_the_block_writes(self):
        visudo = shutil.which("visudo")
        if not visudo:
            self.skipTest("no visudo on this machine")
        p = self.run_block("1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        v = subprocess.run([visudo, "-cf", str(self.r / FILE)], capture_output=True, text=True, timeout=60)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

    def test_the_temporary_file_is_removed(self):
        tdir = self.tmp / "tmpdir"
        tdir.mkdir()
        self.run_block("1", "/dev/null", TMPDIR=str(tdir))
        self.assertEqual(list(tdir.iterdir()), [])


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class VerifyBlock(unittest.TestCase):
    """The verify-image.sh block against a scratch root file system. A stat function answers the owner and group, which an unprivileged test cannot set."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.r = self.tmp / "rootfs"
        (self.r / "etc/sudoers.d").mkdir(parents=True)
        (self.r / "etc/sudoers").write_text("# sudoers\nDefaults\tsecure_path=\"/usr/sbin:/usr/bin\"\nroot ALL=(ALL:ALL) ALL\n%sudo ALL=(ALL:ALL) ALL\n"
                                            "# %wheel ALL=(ALL) NOPASSWD: ALL\n@includedir /etc/sudoers.d\n", encoding="utf-8")
        (self.r / "home/droidian/.ssh").mkdir(parents=True)

    def dev_image(self, key=True, body=None, mode=0o440):
        f = self.r / FILE
        f.unlink(missing_ok=True)  # the previous fixture may be 0440, even for an unprivileged test runner
        f.write_bytes((LINE + "\n").encode() if body is None else body.encode())
        f.chmod(mode)
        if key:
            (self.r / "home/droidian/.ssh/authorized_keys").write_text(TEST_KEY, encoding="utf-8")

    def run_verify(self, owner="0:0", expect=None, path=None):
        script = self.tmp / "verify.sh"
        script.write_text('fail=0; r=%s; here=%s; chome=/home/droidian\n'
                          'check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n'
                          'stat() { if [ "$1 $2" = "-c %%u:%%g" ]; then echo %s; else command stat "$@"; fi; }\n'
                          '%s\nexit $fail\n' % (self.r, PKG, owner, block(*VERIFY_BLOCK)))
        env = {k: v for k, v in os.environ.items() if k != "OPOS_DEV_SUDO"}
        if expect is not None:
            env["OPOS_DEV_SUDO"] = expect
        if path is not None:
            env["PATH"] = path
        return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=120)

    def assert_clean(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("FAIL", p.stdout)

    def test_a_dev_sudo_image_passes_and_says_so(self):
        self.dev_image()
        p = self.run_verify()
        self.assert_clean(p)
        self.assertRegex(p.stdout, r"PASS DEV sudo: /etc/sudoers\.d/90-opos-dev is a plain root-owned 0440 file holding exactly")
        self.assertIn("PASS DEV sudo: no other line in /etc/sudoers or /etc/sudoers.d grants NOPASSWD", p.stdout)
        self.assertIn("NOTE: DEV image: droidian has passwordless sudo", p.stdout)
        self.assertIn("must never be carried or shared", p.stdout)
        self.assertNotIn("no dev sudo", p.stdout)

    def test_an_image_without_the_file_passes(self):
        p = self.run_verify()
        self.assert_clean(p)
        self.assertIn("PASS no dev sudo: nothing in /etc/sudoers or /etc/sudoers.d grants NOPASSWD", p.stdout)
        self.assertIn("NOTE: no passwordless sudo in this image", p.stdout)
        self.assertNotIn("DEV sudo", p.stdout)

    def test_a_file_without_the_dev_ssh_key_fails(self):
        self.dev_image(key=False)
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV sudo: /etc/sudoers.d/90-opos-dev is a plain", p.stdout)

    def test_a_file_that_says_anything_else_fails(self):
        for body in (LINE + "\n\n", LINE, "# dev\n" + LINE + "\n", "droidian ALL=(ALL) NOPASSWD: ALL\n", "droidian ALL=(ALL:ALL) NOPASSWD:ALL\n",
                     "root ALL=(ALL) NOPASSWD:ALL\n", "%sudo ALL=(ALL) NOPASSWD:ALL\n", LINE + "\nroot ALL=(ALL) ALL\n", "", "droidian ALL=(ALL) ALL\n",
                     "droidian ALL=(ALL) NOPASSWD:ALL # x\n"):
            with self.subTest(body=body):
                self.dev_image(body=body)
                p = self.run_verify()
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("FAIL DEV sudo: /etc/sudoers.d/90-opos-dev is a plain", p.stdout)

    def test_a_file_with_the_wrong_mode_fails(self):
        for mode in (0o644, 0o640, 0o444, 0o660, 0o400, 0o666):
            with self.subTest(mode=oct(mode)):
                self.dev_image(mode=mode)
                p = self.run_verify()
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("FAIL DEV sudo: /etc/sudoers.d/90-opos-dev is a plain", p.stdout)

    def test_a_sudoers_file_with_a_special_mode_fails(self):
        self.dev_image(mode=0o4440)
        if (self.r / FILE).stat().st_mode & 0o7777 != 0o4440:
            self.skipTest("this filesystem strips setuid on the scratch sudoers file")
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV sudo: /etc/sudoers.d/90-opos-dev is a plain", p.stdout)

    def test_a_file_with_the_wrong_owner_fails(self):
        self.dev_image()
        for owner in ("1000:1000", "0:1000", "1000:0", "0:27", ""):
            with self.subTest(owner=owner):
                p = self.run_verify(owner=owner or "''")
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("FAIL DEV sudo: /etc/sudoers.d/90-opos-dev is a plain", p.stdout)

    def test_a_link_instead_of_a_file_fails(self):
        self.dev_image()
        (self.r / FILE).unlink()
        target = self.tmp / "t"
        target.write_text(LINE + "\n", encoding="utf-8")
        target.chmod(0o440)
        (self.r / FILE).symlink_to(target)
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV sudo: /etc/sudoers.d/90-opos-dev is a plain", p.stdout)

    def test_another_grant_next_to_the_dev_file_fails(self):
        self.dev_image()
        (self.r / "etc/sudoers.d/99-other").write_text("root ALL=(ALL) NOPASSWD:ALL\n", encoding="utf-8")
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV sudo: no other line", p.stdout)
        self.assertIn("PASS DEV sudo: /etc/sudoers.d/90-opos-dev is a plain", p.stdout)

    def test_a_grant_in_the_main_sudoers_file_fails_the_dev_image_too(self):
        self.dev_image()
        with open(self.r / "etc/sudoers", "a", encoding="utf-8") as f:
            f.write("%sudo ALL=(ALL) NOPASSWD: ALL\n")
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV sudo: no other line", p.stdout)

    def test_a_grant_in_an_image_without_the_file_fails(self):
        (self.r / "etc/sudoers.d/99-other").write_text("droidian ALL=(ALL) NOPASSWD:ALL\n", encoding="utf-8")
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL no dev sudo", p.stdout)
        with open(self.r / "etc/sudoers", "a", encoding="utf-8") as f:
            f.write("droidian ALL=(ALL) NOPASSWD: ALL\n")
        (self.r / "etc/sudoers.d/99-other").unlink()
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL no dev sudo", p.stdout)

    def test_a_commented_grant_is_not_a_grant(self):
        (self.r / "etc/sudoers.d/99-note").write_text("# droidian ALL=(ALL) NOPASSWD:ALL\n   # root ALL=(ALL) NOPASSWD:ALL\n", encoding="utf-8")
        self.assert_clean(self.run_verify())

    def test_the_expectation_from_the_build_variable_is_held_both_ways(self):
        self.dev_image()
        p = self.run_verify(expect="1")
        self.assert_clean(p)
        self.assertIn("PASS OPOS_DEV_SUDO=1: the dev sudoers file is in the image", p.stdout)
        p = self.run_verify(expect="0")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL OPOS_DEV_SUDO=0: no dev sudoers file is in the image", p.stdout)
        (self.r / FILE).unlink()
        p = self.run_verify(expect="0")
        self.assert_clean(p)
        self.assertIn("PASS OPOS_DEV_SUDO=0: no dev sudoers file is in the image", p.stdout)
        p = self.run_verify(expect="1")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL OPOS_DEV_SUDO=1: the dev sudoers file is in the image", p.stdout)

    def test_without_the_variable_the_expectation_is_not_checked(self):
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    self.dev_image()
                p = self.run_verify()
                self.assert_clean(p)
                self.assertNotIn("OPOS_DEV_SUDO=", p.stdout)

    def test_visudo_is_asked_when_the_builder_has_it_and_its_refusal_fails_the_image(self):
        self.dev_image()
        bad = tool_dir(self.tmp / "badbin", ["cmp", "grep", "stat"], {"visudo": "#!/bin/sh\nexit 1\n"})
        p = self.run_verify(path=bad + os.pathsep + os.environ["PATH"])
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV sudo: visudo accepts the file", p.stdout)
        good = tool_dir(self.tmp / "goodbin", ["cmp", "grep", "stat"], {"visudo": "#!/bin/sh\nexit 0\n"})
        p = self.run_verify(path=good + os.pathsep + os.environ["PATH"])
        self.assert_clean(p)
        self.assertIn("PASS DEV sudo: visudo accepts the file", p.stdout)

    def test_a_builder_without_visudo_says_so_and_still_compares_the_bytes(self):
        self.dev_image()
        only = tool_dir(self.tmp / "novisudo", ["cmp", "grep", "stat", "python3", "ssh-keygen"])
        p = self.run_verify(path=only)
        self.assert_clean(p)
        self.assertIn("NOTE: no visudo on this builder", p.stdout)
        self.assertNotIn("visudo accepts", p.stdout)

    def test_the_real_visudo_accepts_the_verified_file(self):
        if not shutil.which("visudo"):
            self.skipTest("no visudo on this machine")
        self.dev_image()
        p = self.run_verify()
        self.assert_clean(p)
        self.assertIn("PASS DEV sudo: visudo accepts the file", p.stdout)


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class VerifyExpectCheck(unittest.TestCase):
    def run_block(self, sudo=None):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "run.sh"
            script.write_text("set -u\n%s\necho BLOCK-DONE\n" % block(*EXPECT_BLOCK))
            env = {k: v for k, v in os.environ.items() if k not in ("OPOS_DEV_SUDO",)}
            if sudo is not None:
                env["OPOS_DEV_SUDO"] = sudo
            return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=60)

    def test_unset_empty_zero_and_one_are_accepted(self):
        for sudo in (None, "", "0", "1"):
            with self.subTest(sudo=sudo):
                p = self.run_block(sudo)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("BLOCK-DONE", p.stdout)

    def test_anything_else_is_refused_before_the_image_is_touched(self):
        for sudo in ("2", "yes", "true", "1 ", "on"):
            with self.subTest(sudo=sudo):
                p = self.run_block(sudo)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertNotIn("BLOCK-DONE", p.stdout)
                self.assertIn("OPOS_DEV_SUDO must be 1, 0 or unset", p.stderr)


class SelftestWarning(TS.SelftestCase):
    def test_the_file_makes_a_dev_warning_with_the_agreed_words(self):
        self.f.put("etc/sudoers.d/90-opos-dev", LINE + "\n", mode=0o440)
        r = self.run_st()
        self.assertEqual(r.status("dev.sudo"), "WARN")
        self.assertEqual(r.ev("dev.sudo"), "DEV image: droidian has passwordless sudo; an image meant to be carried has none")
        self.assertEqual(r.rc, 0, "a dev image is a warning, not a failure")
        self.assertEqual(r.by("FAIL"), [], r.text)

    def test_without_the_file_it_passes_and_nothing_warns(self):
        r = self.run_st()
        self.assertEqual(r.status("dev.sudo"), "PASS")
        self.assertEqual(r.by("WARN"), [], r.text)

    def test_a_link_in_the_place_of_the_file_counts_as_the_file(self):
        (self.f.root / "etc/sudoers.d").mkdir(parents=True)
        (self.f.root / "etc/sudoers.d/90-opos-dev").symlink_to(self.f.tmp / "nowhere")
        self.assertEqual(self.run_st().status("dev.sudo"), "WARN")

    def test_only_dev_shows_it_next_to_the_other_dev_markers(self):
        self.f.put("etc/sudoers.d/90-opos-dev", LINE + "\n", mode=0o440)
        r = self.run_st("--only", "dev")
        self.assertEqual(sorted(r.rows), ["dev.build-mode", "dev.devtools", "dev.span", "dev.ssh-key", "dev.sudo", "dev.time-drop"])
        self.assertEqual(self.run_st("--only", "dev.sudo").status("dev.sudo"), "WARN")

    def test_the_check_reads_and_runs_nothing_of_sudo(self):
        self.f.put("etc/sudoers.d/90-opos-dev", LINE + "\n", mode=0o440)
        before = self.f.snapshot()
        self.run_st()
        self.assertEqual(before, self.f.snapshot())
        self.assertFalse([ln for ln in self.f.calls() if "sudo" in ln.split()[0]], self.f.calls())


if __name__ == "__main__":
    unittest.main()
