# SPDX-License-Identifier: GPL-3.0-or-later
"""OPOS_DEV_SPAN: a dev checkpoint image that starts opos-server with the two-screen span on.

packaging/duo-os/opos-server.service ships `#Environment=OPOS_SPAN=675,42` commented out until the layout has been checked on the phone, and the
pane surfaces paint only while a span is on. `build-image.sh` therefore has a dev-only knob, `OPOS_DEV_SPAN=1`, that works only together with
`OPOS_DEV_SSH_PUBKEY` and writes the systemd drop-in `opos-server.service.d/30-opos-dev-span.conf`; `verify-image.sh` checks the drop-in. The
blocks of both scripts are run here against a scratch root file system, the way the clock and OTA blocks are; no image, mount or qemu is involved.
The verify block's check that "the image's python3 builds the page" runs this builder's python3 on the repository's own sources."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging" / "duo-os"
SH = shutil.which("sh")
PY = shutil.which("python3") or shutil.which("python")
DROPIN = "etc/systemd/system/opos-server.service.d/30-opos-dev-span.conf"


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


CHECK_BLOCK = ("build-image.sh", "# --- dev span: CHECK BEGIN", "# --- dev span: CHECK END")
WRITE_BLOCK = ("build-image.sh", "# --- dev span: BEGIN", "# --- dev span: END")
VERIFY_BLOCK = ("verify-image.sh", "# --- dev span: BEGIN", "# --- dev span: END")


class StaticPins(unittest.TestCase):
    def test_each_block_is_delimited_once(self):
        for spec in (CHECK_BLOCK, WRITE_BLOCK, VERIFY_BLOCK):
            with self.subTest(spec=spec):
                self.assertTrue(block(*spec).strip())

    def test_the_check_runs_before_the_output_is_touched_and_the_writer_sits_after_the_dev_key_and_before_the_updater(self):
        t = read("packaging/duo-os/build-image.sh")
        self.assertLess(t.index("# --- dev span: CHECK END"), t.index('cp --sparse=always "$base" "$out"'))
        self.assertLess(t.index("# --- dev span: CHECK END"), t.index("mount -o loop"))
        self.assertLess(t.index("OPOS_KIOSK_DEBUG_PORT=9222"), t.index("# --- dev span: BEGIN"))
        self.assertLess(t.index("# --- dev span: END"), t.index("# ---- BEGIN OPOS OTA updates"))
        self.assertLess(t.index("install -d \"$r/etc/systemd/system/opos-server.service.d\""), t.index("# --- dev span: BEGIN"))

    def test_the_unit_still_ships_the_span_off(self):
        unit = read("packaging/duo-os/opos-server.service")
        self.assertIn("\n#Environment=OPOS_SPAN=675,42\n", unit)
        self.assertNotRegex(unit, r"(?m)^\s*Environment=.*OPOS_SPAN")

    def test_one_drop_in_name_everywhere(self):
        name = "30-opos-dev-span.conf"
        for path in ("packaging/duo-os/build-image.sh", "packaging/duo-os/verify-image.sh", "packaging/duo-os/README.md", "docs/duo-os-final.md"):
            self.assertIn(name, read(path), path)
        self.assertRegex(read("packaging/duo-os/build-image.sh"), r"(?m)^rm -f \"\$r/etc/systemd/system/opos-server\.service\.d/30-opos-dev-span\.conf\"$")

    def test_the_knob_is_documented_where_the_other_switches_are(self):
        readme = read("packaging/duo-os/README.md")
        self.assertRegex(readme, r"(?m)^\| `OPOS_DEV_SPAN` \| build input, dev images only \|")
        self.assertIn("`OPOS_DEV_SPAN=1` together with `OPOS_DEV_SSH_PUBKEY`", readme)
        self.assertIn("OPOS_DEV_SPAN", read("docs/duo-os-final.md"))
        self.assertIn("OPOS_DEV_SPAN", read("CHANGELOG.md"))

    def test_the_scripts_parse_and_have_no_carriage_returns(self):
        for n in ("build-image.sh", "verify-image.sh"):
            raw = (PKG / n).read_bytes()
            self.assertNotIn(b"\r", raw, n)
            if SH:
                r = subprocess.run([SH, "-n", str(PKG / n)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, n + ": " + r.stderr)

    def test_the_verify_block_does_not_start_another_server(self):
        code = "\n".join(ln for ln in block(*VERIFY_BLOCK).splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("serve ", code)
        self.assertNotIn("-m opos.web", code)
        self.assertNotRegex(code, r"(?<![/\w])python3 (?!-I\b)-")  # the builder's python3, if used at all, is isolated


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class BuildBlocks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.r = self.tmp / "rootfs"
        (self.r / "etc/systemd/system/opos-server.service.d").mkdir(parents=True)

    def run_block(self, spec, span=None, key=None, extra=""):
        script = self.tmp / "run.sh"
        script.write_text("set -eu\nr=%s\n%s\n%secho BLOCK-DONE\n" % (self.r, block(*spec), extra))
        env = {k: v for k, v in os.environ.items() if k not in ("OPOS_DEV_SPAN", "OPOS_DEV_SSH_PUBKEY")}
        if span is not None:
            env["OPOS_DEV_SPAN"] = span
        if key is not None:
            env["OPOS_DEV_SSH_PUBKEY"] = key
        return subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=60)

    def test_the_check_accepts_unset_empty_zero_and_one_with_a_key(self):
        for span, key in ((None, None), ("", None), ("0", None), (None, "/dev/null"), ("0", "/dev/null"), ("1", "/dev/null")):
            with self.subTest(span=span, key=key):
                p = self.run_block(CHECK_BLOCK, span, key)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("BLOCK-DONE", p.stdout)

    def test_the_check_refuses_the_knob_without_the_dev_key(self):
        for key in (None, ""):
            with self.subTest(key=key):
                p = self.run_block(CHECK_BLOCK, "1", key)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertNotIn("BLOCK-DONE", p.stdout)
                self.assertIn("OPOS_DEV_SPAN=1 is a dev-image option", p.stderr)
                self.assertIn("OPOS_DEV_SSH_PUBKEY", p.stderr)

    def test_the_check_refuses_other_values(self):
        for span in ("2", "yes", "true", "on", "1 ", "675,42"):
            with self.subTest(span=span):
                p = self.run_block(CHECK_BLOCK, span, "/dev/null")
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn("OPOS_DEV_SPAN must be 1", p.stderr)

    def test_a_dev_image_gets_exactly_the_drop_in(self):
        p = self.run_block(WRITE_BLOCK, "1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        f = self.r / DROPIN
        self.assertTrue(f.is_file() and not f.is_symlink())
        lines = f.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("# DEV IMAGE"), lines)
        self.assertEqual([ln for ln in lines if not ln.startswith("#")], ["[Service]", "Environment=OPOS_SPAN=675,42"])
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o644")
        self.assertIn("DEV IMAGE: opos-server runs with OPOS_SPAN=675,42", p.stdout)
        self.assertIn(DROPIN, p.stdout, "the manifest line with the file's hash")

    def test_the_drop_in_is_the_only_file_written(self):
        before = {str(x.relative_to(self.r)) for x in self.r.rglob("*")}
        self.run_block(WRITE_BLOCK, "1", "/dev/null")
        after = {str(x.relative_to(self.r)) for x in self.r.rglob("*")}
        self.assertEqual(after - before, {DROPIN})

    def test_no_drop_in_without_the_knob_or_without_the_key(self):
        for span, key in ((None, None), (None, "/dev/null"), ("", "/dev/null"), ("0", "/dev/null"), ("1", None), ("1", "")):
            with self.subTest(span=span, key=key):
                p = self.run_block(WRITE_BLOCK, span, key)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertFalse((self.r / DROPIN).exists())
                self.assertNotIn("DEV IMAGE", p.stdout)

    def test_a_stale_drop_in_from_an_earlier_dev_image_is_removed(self):
        stale = self.r / DROPIN
        stale.write_text("[Service]\nEnvironment=OPOS_SPAN=700,42\n", encoding="utf-8")
        p = self.run_block(WRITE_BLOCK, None, "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(stale.exists() or stale.is_symlink())
        stale.write_text("old\n", encoding="utf-8")
        p = self.run_block(WRITE_BLOCK, "1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("old", stale.read_text(encoding="utf-8"))
        self.assertIn("Environment=OPOS_SPAN=675,42", stale.read_text(encoding="utf-8"))

    def test_a_symlink_in_the_way_is_replaced_not_followed(self):
        target = self.tmp / "elsewhere"
        target.write_text("keep\n", encoding="utf-8")
        link = self.r / DROPIN
        link.symlink_to(target)
        p = self.run_block(WRITE_BLOCK, "1", "/dev/null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(link.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")


@unittest.skipUnless(SH and PY and os.name == "posix", "needs a POSIX sh and python3")
class VerifyBlock(unittest.TestCase):
    """The verify-image.sh block against a scratch root file system built with the real unit, the real network drop-in and, for a dev image,
    the drop-in the build block writes. The image's python3 under qemu is this builder's python3 on the repository sources."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.r = self.tmp / "rootfs"
        d = self.r / "etc/systemd/system/opos-server.service.d"
        d.mkdir(parents=True)
        shutil.copy(PKG / "opos-server.service", self.r / "etc/systemd/system/opos-server.service")
        shutil.copy(PKG / "opos-network.conf", d / "20-opos-network.conf")
        (self.r / "home/droidian/.ssh").mkdir(parents=True)
        (self.tmp / "work").mkdir()

    def dev_image(self, key=True):
        script = self.tmp / "build.sh"
        script.write_text("set -eu\nr=%s\n%s\n" % (self.r, block(*WRITE_BLOCK)))
        env = dict(os.environ, OPOS_DEV_SPAN="1", OPOS_DEV_SSH_PUBKEY="/dev/null")
        p = subprocess.run([SH, str(script)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        if key:
            (self.r / "home/droidian/.ssh/authorized_keys").write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")

    def run_verify(self, py=None):
        script = self.tmp / "verify.sh"
        stat = "" if os.geteuid() == 0 else 'stat() { if [ "$1 $2" = "-c %u:%g" ]; then echo 0:0; else command stat "$@"; fi; }\n'
        py = py or 'py() { PYTHONPATH="%s/src" PYTHONDONTWRITEBYTECODE=1 PYTHONSAFEPATH=1 "%s" "$@"; }' % (ROOT, PY)
        script.write_text('fail=0; r=%s; w=$r/etc/systemd/system; chome=/home/droidian; work=%s; smoke_pid=\n'
                          'check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n%s\n%s\n%s\nexit $fail\n'
                          % (self.r, self.tmp / "work", stat, py, block(*VERIFY_BLOCK)))
        return subprocess.run([SH, str(script)], capture_output=True, text=True, timeout=120)

    def assert_clean(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("FAIL", p.stdout)

    def test_a_dev_image_passes_and_its_page_is_built_for_the_drop_in_value(self):
        self.dev_image()
        p = self.run_verify()
        self.assert_clean(p)
        self.assertRegex(p.stdout, r"PASS DEV span: the drop-in is a plain root-owned 0644 file")
        self.assertRegex(p.stdout, r"PASS DEV span: the image's python3 builds the two-screen page")
        self.assertIn("dev span page: gate 1055 px, --pane 675px, --hinge 42px, Settings readout", p.stdout)
        self.assertIn("NOTE: DEV image: opos-server runs with OPOS_SPAN=675,42", p.stdout)

    def test_an_image_without_the_drop_in_passes_and_sets_no_span(self):
        p = self.run_verify()
        self.assert_clean(p)
        self.assertIn("PASS no dev span: nothing in opos-server.service or its drop-ins sets OPOS_SPAN", p.stdout)
        self.assertIn("NOTE: no dev span in this image", p.stdout)
        self.assertNotIn("DEV span", p.stdout)

    def test_a_drop_in_without_the_dev_ssh_key_fails(self):
        self.dev_image(key=False)
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV span: the drop-in is a plain", p.stdout)

    def test_a_drop_in_that_says_anything_else_fails(self):
        for body in ("[Service]\nEnvironment=OPOS_SPAN=700,42\n", "[Service]\nEnvironment=OPOS_SPAN=675,42\nEnvironment=OPOS_OSK=off\n",
                     "[Service]\nEnvironment=OPOS_SPAN=off\n", "[Unit]\nDescription=x\n[Service]\nEnvironment=OPOS_SPAN=675,42\n", ""):
            with self.subTest(body=body):
                self.dev_image()
                (self.r / DROPIN).write_text(body, encoding="utf-8")
                p = self.run_verify()
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("FAIL DEV span: the drop-in is a plain", p.stdout)

    def test_a_drop_in_with_the_wrong_mode_or_a_link_fails(self):
        self.dev_image()
        (self.r / DROPIN).chmod(0o664)
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV span: the drop-in is a plain", p.stdout)
        (self.r / DROPIN).unlink()
        target = self.tmp / "t.conf"
        target.write_text("[Service]\nEnvironment=OPOS_SPAN=675,42\n", encoding="utf-8")
        (self.r / DROPIN).symlink_to(target)
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV span: the drop-in is a plain", p.stdout)

    def test_a_second_setting_of_the_span_in_another_drop_in_fails(self):
        self.dev_image()
        (self.r / "etc/systemd/system/opos-server.service.d/99-other.conf").write_text("[Service]\nEnvironment=OPOS_SPAN=675,42\n", encoding="utf-8")
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV span: the drop-in is a plain", p.stdout)

    def test_a_span_set_outside_a_dev_drop_in_fails_a_non_dev_image(self):
        d = self.r / "etc/systemd/system/opos-server.service.d"
        (d / "99-other.conf").write_text("[Service]\nEnvironment=OPOS_SPAN=675,42\n", encoding="utf-8")
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL no dev span", p.stdout)

    def test_the_unit_with_the_span_switched_on_fails_every_image(self):
        unit = self.r / "etc/systemd/system/opos-server.service"
        unit.write_text(unit.read_text(encoding="utf-8").replace("#Environment=OPOS_SPAN=675,42", "Environment=OPOS_SPAN=675,42"), encoding="utf-8")
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL the span is only a comment in opos-server.service", p.stdout)
        self.assertIn("FAIL no dev span", p.stdout)

    def test_the_unit_with_the_comment_removed_fails(self):
        unit = self.r / "etc/systemd/system/opos-server.service"
        unit.write_text(unit.read_text(encoding="utf-8").replace("#Environment=OPOS_SPAN=675,42\n", ""), encoding="utf-8")
        p = self.run_verify()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL the span is only a comment", p.stdout)

    def test_a_python_that_cannot_build_the_page_fails_the_dev_image(self):
        self.dev_image()
        p = self.run_verify(py="py() { echo 'Traceback: no module named opos' >&2; return 1; }")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("FAIL DEV span: the image's python3 builds the two-screen page", p.stdout)
        self.assertIn("PASS DEV span: the drop-in is a plain", p.stdout)


if __name__ == "__main__":
    unittest.main()
