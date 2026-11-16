# SPDX-License-Identifier: GPL-3.0-or-later
"""The sensorfwd boot timer of the Duo image (stage 1 of the plan: a timer, never a boot-time enable): the three unit
files, the "sensors" block of build-image.sh run against a fake root with stand-in systemctl and friends on PATH, and the
"track sensors" checks of verify-image.sh run against that root and against broken copies of it. Nothing here starts,
enables or talks to systemd; whether systemd on the phone honours these files is a phone check and stays NOT_RUN."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging" / "duo-os"
BUILD = DUO / "build-image.sh"
VERIFY = DUO / "verify-image.sh"
DROPIN = DUO / "opos-sensorfwd-boot.conf"
SERVICE = DUO / "opos-sensorfwd-delayed.service"
TIMER = DUO / "opos-sensorfwd-delayed.timer"
SH = shutil.which("sh")
SYS = "etc/systemd/system"
# What the block may leave in the fake root, and nothing else.
INSTALLED = {
    SYS + "/sensorfwd.service.d/70-opos-boot.conf": DROPIN,
    SYS + "/opos-sensorfwd-delayed.service": SERVICE,
    SYS + "/opos-sensorfwd-delayed.timer": TIMER,
}
LINK = SYS + "/timers.target.wants/opos-sensorfwd-delayed.timer"
# Stand-ins for every program that could start, enable or mask a unit: each records its call and fails.
STARTERS = ("systemctl", "systemd-run", "deb-systemd-helper", "service", "update-rc.d", "invoke-rc.d")


def block(path, begin, end):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^%s.*?\n(.*?)^%s.*?\n" % (re.escape(begin), re.escape(end)), text, re.S | re.M)
    assert m, (path, begin)
    return m.group(1)


def sections(path):
    """The unit file as {section: [key=value lines]}, comments and blank lines left out."""
    out, cur = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            out.setdefault(cur, [])
        else:
            out[cur].append(s)
    return out


class UnitFiles(unittest.TestCase):
    """Exactly the lines the plan lists, and the drop-in changes no line of the port's own unit."""

    def test_the_drop_in_holds_the_kill_switch_and_the_restart_cap_and_nothing_else(self):
        self.assertEqual(sections(DROPIN), {
            "Unit": ["ConditionPathExists=!/etc/opos/sensors-off", "StartLimitIntervalSec=300", "StartLimitBurst=3"],
            "Service": ["Restart=on-failure"],
        })

    def test_the_timer_fires_once_120_s_after_boot_and_is_the_only_thing_enabled(self):
        self.assertEqual(sections(TIMER), {
            "Unit": ["Description=OPOS: start the port's sensor service 120 s after boot"],
            "Timer": ["OnBootSec=120", "Unit=opos-sensorfwd-delayed.service"],
            "Install": ["WantedBy=timers.target"],
        })

    def test_the_service_starts_sensorfwd_once_honours_the_kill_switch_and_cannot_be_enabled_by_itself(self):
        s = sections(SERVICE)
        self.assertEqual(s["Unit"], ["Description=OPOS: start sensorfwd once, from opos-sensorfwd-delayed.timer",
                                     "ConditionPathExists=!/etc/opos/sensors-off"])
        self.assertEqual(s["Service"], ["Type=oneshot", "ExecStart=/usr/bin/systemctl start sensorfwd.service"])
        self.assertNotIn("Install", s, "only the timer can be enabled")

    def test_every_file_carries_the_license_line_first(self):
        for path in (DROPIN, SERVICE, TIMER):
            with self.subTest(path=path.name):
                self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0], "# SPDX-License-Identifier: GPL-3.0-or-later")

    def test_the_kill_switch_is_described_as_blocking_future_starts_with_the_live_stop_commands(self):
        # The sensors-off file only makes the condition skip later starts; a sensorfwd already running keeps running.
        build = block(BUILD, "# --- sensors: BEGIN", "# --- sensors: END")
        readme = (DUO / "README.md").read_text(encoding="utf-8")
        texts = {"build-image.sh": build, "README.md": readme,
                 DROPIN.name: DROPIN.read_text(encoding="utf-8"), SERVICE.name: SERVICE.read_text(encoding="utf-8")}
        for name, text in texts.items():
            with self.subTest(name=name):
                flat = " ".join(ln.lstrip("# ").strip() for ln in text.splitlines())
                self.assertIn("future starts", flat)
                self.assertIn("systemctl disable --now opos-sensorfwd-delayed.timer", flat)
                self.assertIn("systemctl stop sensorfwd", flat)
                self.assertNotIn("keeps it off", flat)


class FakeRoot(unittest.TestCase):
    """A fake root as the port's base ships it, and the build block run on it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name)
        self.r = self.t / "root"
        self.bin = self.t / "bin"
        self.bin.mkdir()
        self.calls = self.t / "calls"
        for name in STARTERS:
            p = self.bin / name
            p.write_text('#!/bin/sh\necho "%s $*" >> "%s"\nexit 1\n' % (name, self.calls))
            p.chmod(0o755)
        self.fresh()

    def fresh(self):
        """A new base: sensorfwd installed and disabled, iio-sensor-proxy masked, nothing of OPOS's."""
        if self.r.exists():
            shutil.rmtree(self.r)
        (self.r / "usr/lib/systemd/system").mkdir(parents=True)
        (self.r / "usr/lib/systemd/system/sensorfwd.service").write_text("[Unit]\nDescription=base\n")
        (self.r / SYS / "multi-user.target.wants").mkdir(parents=True)
        os.symlink("/dev/null", self.r / SYS / "iio-sensor-proxy.service")

    def build(self):
        text = block(BUILD, "# --- sensors: BEGIN", "# --- sensors: END")
        script = self.t / "block.sh"
        script.write_text("set -eu\nr=%s\nhere=%s\n%s" % (self.r, DUO, text))
        p = subprocess.run([SH, str(script)], env={"PATH": "%s:/usr/bin:/bin" % self.bin},
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def tree(self):
        return sorted(str(p.relative_to(self.r)) for p in self.r.rglob("*") if p.is_symlink() or p.is_file())


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class BuildBlock(FakeRoot):
    def test_the_three_files_are_installed_byte_for_byte_with_mode_0644(self):
        self.build()
        for dest, src in INSTALLED.items():
            with self.subTest(dest=dest):
                p = self.r / dest
                self.assertFalse(p.is_symlink())
                self.assertEqual(p.read_bytes(), src.read_bytes())
                self.assertEqual(oct(p.stat().st_mode & 0o777), "0o644")

    def test_only_the_timer_is_enabled_and_only_in_timers_target(self):
        self.build()
        self.assertEqual(os.readlink(self.r / LINK), "/etc/systemd/system/opos-sensorfwd-delayed.timer")
        links = [p for p in self.tree() if p.split("/")[-2].endswith(".wants")]
        self.assertEqual(links, [LINK], "sensorfwd.service and the delayed service are not linked anywhere")

    def test_nothing_else_is_written_no_kill_switch_ships_and_the_base_is_left_as_it_was(self):
        before = self.tree()
        self.build()
        self.assertEqual(sorted(set(self.tree()) - set(before)), sorted(list(INSTALLED) + [LINK]))
        self.assertFalse((self.r / "etc/opos").exists(), "no /etc/opos/sensors-off, no /etc/opos at all")
        self.assertEqual(os.readlink(self.r / SYS / "iio-sensor-proxy.service"), "/dev/null", "the mask stays")
        self.assertEqual((self.r / "usr/lib/systemd/system/sensorfwd.service").read_text(), "[Unit]\nDescription=base\n")

    def test_nothing_is_started_enabled_or_masked_while_building(self):
        self.build()
        self.assertFalse(self.calls.exists(), self.calls.read_text() if self.calls.exists() else "")
        code = [ln for ln in block(BUILD, "# --- sensors: BEGIN", "# --- sensors: END").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        for name in STARTERS:      # as a command word, not inside a file name such as opos-sensorfwd-delayed.service
            with self.subTest(name=name):
                self.assertFalse([ln for ln in code if re.search(r"(^|[\s;&|(/])%s(\s|$)" % re.escape(name), ln)])
        for word in ("sensors-off", "multi-user.target.wants", "graphical.target.wants", "/dev/null"):
            with self.subTest(word=word):
                self.assertFalse([ln for ln in code if word in ln])

    def test_a_second_run_on_the_same_root_changes_nothing(self):
        self.build()
        first = {p: (self.r / p).read_bytes() if not (self.r / p).is_symlink() else os.readlink(self.r / p)
                 for p in self.tree()}
        self.build()
        self.assertEqual({p: (self.r / p).read_bytes() if not (self.r / p).is_symlink() else os.readlink(self.r / p)
                          for p in self.tree()}, first)

    def test_the_block_is_written_once_after_the_native_shell_and_before_the_manifest_is_taken(self):
        text = BUILD.read_text(encoding="utf-8")
        self.assertEqual(text.count("# --- sensors: BEGIN"), 1)
        self.assertLess(text.index("# --- native shell: END"), text.index("# --- sensors: BEGIN"))
        self.assertLess(text.index("# --- sensors: END"), text.index("# --- identity finish: BEGIN"))
        dev = text[text.index("# --- dev span: BEGIN"):text.index("# --- dev span: END")]
        self.assertNotIn("sensor", dev)


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class VerifyChecks(FakeRoot):
    def verify(self):
        text = block(VERIFY, "# --- track sensors: BEGIN", "# --- track sensors: END")
        script = self.t / "verify.sh"
        script.write_text('r=%s; w=$r/etc/systemd/system; fail=0\n'
                          'check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n'
                          '%s\nexit $fail\n' % (self.r, text))
        return subprocess.run([SH, str(script)], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60)

    def edit(self, rel, old, new):
        p = self.r / rel
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text)
        p.write_text(text.replace(old, new), encoding="utf-8")

    def fails(self, what):
        p = self.verify()
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("FAIL the " + what, p.stdout)

    def test_a_built_root_passes_both_checks(self):
        self.build()
        p = self.verify()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.count("PASS "), 2, p.stdout)

    def test_a_missing_timer_service_or_link_fails(self):
        for rel in (SYS + "/opos-sensorfwd-delayed.timer", SYS + "/opos-sensorfwd-delayed.service", LINK):
            with self.subTest(rel=rel):
                self.fresh()
                self.build()
                (self.r / rel).unlink()
                self.fails("sensor boot timer")

    def test_a_link_to_somewhere_else_fails(self):
        self.build()
        (self.r / LINK).unlink()
        os.symlink("/dev/null", self.r / LINK)
        self.fails("sensor boot timer")

    def test_a_timer_with_another_delay_fails_even_one_that_starts_with_120(self):
        for delay in ("1200", "12", "0"):
            with self.subTest(delay=delay):
                self.fresh()
                self.build()
                self.edit(SYS + "/opos-sensorfwd-delayed.timer", "OnBootSec=120\n", "OnBootSec=%s\n" % delay)
                self.fails("sensor boot timer")

    def test_a_timer_that_names_another_unit_or_no_unit_fails(self):
        for new in ("Unit=iio-sensor-proxy.service\n", "Unit=opos-sensorfwd-delayed.service.off\n", ""):
            with self.subTest(new=new):
                self.fresh()
                self.build()
                self.edit(SYS + "/opos-sensorfwd-delayed.timer", "Unit=opos-sensorfwd-delayed.service\n", new)
                self.fails("sensor boot timer")

    def test_a_service_that_does_not_start_sensorfwd_or_ignores_the_kill_switch_fails(self):
        for old, new in (("start sensorfwd.service", "start iio-sensor-proxy.service"),
                         ("ConditionPathExists=!/etc/opos/sensors-off\n", "")):
            with self.subTest(old=old):
                self.fresh()
                self.build()
                self.edit(SYS + "/opos-sensorfwd-delayed.service", old, new)
                self.fails("sensor boot timer")

    def test_sensorfwd_enabled_for_any_target_fails_even_through_a_link_that_does_not_resolve_here(self):
        # verify-image.sh reads a mounted image: an absolute link into it points at the host, so it does not resolve.
        for target in ("multi-user.target.wants", "graphical.target.wants", "timers.target.wants"):
            with self.subTest(target=target):
                self.fresh()
                self.build()
                (self.r / SYS / target).mkdir(exist_ok=True)
                os.symlink("/etc/systemd/system/sensorfwd.service", self.r / SYS / target / "sensorfwd.service")
                self.fails("sensor boot timer")

    def test_a_shipped_kill_switch_fails_as_a_file_or_as_a_link(self):
        for kind in ("file", "link"):
            with self.subTest(kind=kind):
                self.fresh()
                self.build()
                off = self.r / "etc/opos/sensors-off"
                off.parent.mkdir(parents=True, exist_ok=True)
                if kind == "file":
                    off.write_text("")
                else:
                    os.symlink("/etc/opos/nowhere", off)
                self.fails("sensor boot timer")

    def test_a_drop_in_without_the_kill_switch_or_the_restart_cap_fails(self):
        d = SYS + "/sensorfwd.service.d/70-opos-boot.conf"
        for old, new in (("ConditionPathExists=!/etc/opos/sensors-off\n", ""),
                         ("ConditionPathExists=!/etc/opos/sensors-off", "ConditionPathExists=/etc/opos/sensors-off"),
                         ("ConditionPathExists=!/etc/opos/sensors-off\n", "ConditionPathExists=!/etc/opos/sensors-offline\n"),
                         ("Restart=on-failure", "Restart=always"),
                         ("StartLimitIntervalSec=300\n", ""),
                         ("StartLimitBurst=3", "StartLimitBurst=30")):
            with self.subTest(old=old, new=new):
                self.fresh()
                self.build()
                self.edit(d, old, new)
                self.fails("sensorfwd drop-in")
        self.fresh()
        self.build()
        (self.r / d).unlink()
        self.fails("sensorfwd drop-in")


if __name__ == "__main__":
    unittest.main()
