# SPDX-License-Identifier: GPL-3.0-or-later
"""Packaging of the Duo OS on-screen keyboard: the unit files, the opt-in launcher run for real with a fake keyboard, build and verify blocks, docs."""
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging" / "duo-os"
SH = shutil.which("sh")


def lines(path):
    return (DUO / path).read_text(encoding="utf-8").splitlines()


class UnitsTest(unittest.TestCase):
    def test_server_unit_turns_the_in_page_keyboard_on_and_lets_a_file_override_it(self):
        ls = lines("opos-server.service")
        self.assertIn("Environment=OPOS_OSK=1", ls)
        self.assertIn("EnvironmentFile=-/etc/default/opos-osk", ls)
        self.assertLess(ls.index("[Service]"), ls.index("Environment=OPOS_OSK=1"), "in [Service]")
        self.assertLess(ls.index("[Service]"), ls.index("EnvironmentFile=-/etc/default/opos-osk"))
        self.assertLess(ls.index("Environment=OPOS_OSK=1"), ls.index("ExecStart=/usr/bin/python3 -m opos.web --port 8765 --live-battery"))

    def test_osk_unit_is_enabled_but_idle_and_never_starts_beside_phosh(self):
        ls = lines("opos-osk.service")
        self.assertEqual(ls[0], "# SPDX-License-Identifier: GPL-3.0-or-later")
        for want in ("Requisite=opos.service", "After=opos.service", "PartOf=opos.service", "User=droidian",
                     "EnvironmentFile=-/etc/default/opos-osk", "ExecStart=/usr/libexec/opos/opos-osk", "Restart=on-failure",
                     "WantedBy=graphical.target"):
            self.assertIn(want, ls, want)
        self.assertNotIn("Requires=opos.service", ls, "Requires would start OPOS beside phosh")
        self.assertFalse([l for l in ls if l.startswith(("BindsTo=", "WantedBy=multi-user"))])
        self.assertIn("StartLimitBurst=5", ls)

    def test_scripts_parse(self):
        if SH is None:
            self.skipTest("no sh")
        for name in ("opos-osk", "build-image.sh", "verify-image.sh"):
            r = subprocess.run([SH, "-n", str(DUO / name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)
        self.assertTrue((DUO / "opos-osk").read_text(encoding="utf-8").startswith("#!/bin/sh\n# SPDX-License-Identifier: GPL-3.0-or-later\n"))
        for name in ("opos-osk", "opos-osk.service", "OSK.md"):
            self.assertNotIn(b"\r", (DUO / name).read_bytes(), name)


@unittest.skipIf(SH is None or sys.platform == "win32", "needs a POSIX sh and unix sockets")
class LauncherTest(unittest.TestCase):
    """opos-osk run for real: a fake runtime dir with a Wayland socket and a fake keyboard that records its environment."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osk"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.out = self.tmp / "out.txt"
        self.fake = self.tmp / "fake-stevia"
        self.fake.write_text('#!/bin/sh\n{ echo "ARGS=$*"; echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"; echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR";'
                             ' echo "GDK_BACKEND=$GDK_BACKEND"; echo "DBUS=$DBUS_SESSION_BUS_ADDRESS"; } > "$OUT"\n')
        self.fake.chmod(0o755)
        self.socks = []

    def socket(self, name):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(self.run_dir / name))
        self.socks.append(s)
        self.addCleanup(s.close)

    def run_osk(self, wait="2", extra=None, path=None, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("OPOS_OSK", "DBUS_SESSION", "WAYLAND", "XDG_RUNTIME"))}
        e.update(OUT=str(self.out), XDG_RUNTIME_DIR=str(self.run_dir), OPOS_OSK_BIN=str(self.fake), OPOS_OSK_WAIT=wait, OPOS_OSK_GSETTINGS="0")
        e.update(env)
        if path is not None:
            e["PATH"] = path + os.pathsep + e.get("PATH", "")
        return subprocess.run([SH, str(DUO / "opos-osk")] + (extra or []), capture_output=True, text=True, env=e, timeout=30)

    def recorded(self):
        return dict(l.split("=", 1) for l in self.out.read_text().splitlines())

    def test_it_does_nothing_unless_stevia_is_asked_for(self):
        self.socket("wayland-0")
        for env in ({}, {"OPOS_OSK": "1"}, {"OPOS_OSK": "web"}, {"OPOS_OSK": "off"}, {"OPOS_OSK": "0"}, {"OPOS_OSK": "Stevia"}):
            r = self.run_osk(**env)
            self.assertEqual(r.returncode, 0, (env, r.stderr))
            self.assertFalse(self.out.exists(), env)
            self.assertIn("nothing to start", r.stdout)

    def test_it_runs_stevia_on_the_wayland_socket_it_finds(self):
        self.socket("wayland-3")
        (self.run_dir / "wayland-3.lock").write_text("")  # phoc's lock file is not the socket
        r = self.run_osk(OPOS_OSK="stevia")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = self.recorded()
        self.assertEqual(got["ARGS"], "--allow-replacement")
        self.assertEqual(got["WAYLAND_DISPLAY"], "wayland-3")
        self.assertEqual(got["XDG_RUNTIME_DIR"], str(self.run_dir))
        self.assertEqual(got["GDK_BACKEND"], "wayland")
        self.assertEqual(got["DBUS"], f"unix:path={self.run_dir}/bus")
        self.assertIn("starting", r.stdout)

    def test_it_keeps_a_session_bus_it_is_given(self):
        self.socket("wayland-0")
        r = self.run_osk(OPOS_OSK="stevia", DBUS_SESSION_BUS_ADDRESS="unix:path=/tmp/private-bus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.recorded()["DBUS"], "unix:path=/tmp/private-bus")

    def test_it_waits_for_phoc_and_gives_up_politely(self):
        r = self.run_osk(wait="1", OPOS_OSK="stevia")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no Wayland socket", r.stderr)
        self.assertFalse(self.out.exists())
        (self.run_dir / "wayland-0.lock").write_text("")
        (self.run_dir / "wayland-1").write_text("a regular file")
        r = self.run_osk(wait="1", OPOS_OSK="stevia")
        self.assertEqual(r.returncode, 1, "a lock file and a regular file are not sockets")

    def test_a_missing_keyboard_is_not_a_failure_loop(self):
        self.socket("wayland-0")
        r = self.run_osk(OPOS_OSK="stevia", OPOS_OSK_BIN=str(self.tmp / "absent"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("is not installed", r.stdout)
        self.assertFalse(self.out.exists())

    def test_it_enables_the_screen_keyboard_setting_unless_told_not_to(self):
        self.socket("wayland-0")
        bindir = self.tmp / "bin"
        bindir.mkdir()
        log = self.tmp / "gsettings.txt"
        g = bindir / "gsettings"
        g.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
        g.chmod(0o755)
        r = self.run_osk(path=str(bindir), OPOS_OSK="stevia", OPOS_OSK_GSETTINGS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(log.read_text().strip(), "set org.gnome.desktop.a11y.applications screen-keyboard-enabled true")
        log.unlink()
        r = self.run_osk(path=str(bindir), OPOS_OSK="stevia", OPOS_OSK_GSETTINGS="0")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(log.exists())


class ImageScriptsTest(unittest.TestCase):
    def block(self, name, start, end):
        t = (DUO / name).read_text(encoding="utf-8")
        self.assertEqual(t.count(start), 1, name)
        a = t.index(start)
        b = t.index(end, a)
        return t[a:b]

    def test_build_image_installs_the_launcher_the_unit_and_the_default_file(self):
        t = (DUO / "build-image.sh").read_text(encoding="utf-8")
        b = self.block("build-image.sh", "# --- track osk", "# --- end track osk")
        self.assertIn('install -m 0755 "$here/opos-osk" "$r/usr/libexec/opos/opos-osk"', b)
        self.assertIn('install -m 0644 "$here/opos-osk.service" "$r/etc/systemd/system/opos-osk.service"', b)
        self.assertIn('ln -sf /etc/systemd/system/opos-osk.service "$r/etc/systemd/system/graphical.target.wants/opos-osk.service"', b)
        self.assertIn('> "$r/etc/default/opos-osk"', b)
        self.assertIn("'#OPOS_OSK=1'", b, "the shipped file has the setting commented out")
        self.assertNotRegex(b, r"\n\s*OPOS_OSK=")
        self.assertLess(t.index("# --- track osk"), t.index("\nsync\n"), "before the image is synced and unmounted")
        self.assertIn("src/opos/osk_static", b, "says where the in-page keyboard's files come from")
        self.assertIn("g archive \"$ref\" src", t, "the OPOS tree, osk_static included, is archived whole")

    def test_the_default_file_the_image_gets_is_all_comments(self):
        fmt = "printf '%s\\n'"
        b = self.block("build-image.sh", fmt + " '# On-screen keyboard.", "chmod 0644 \"$r/etc/default/opos-osk\"")
        args = [l.strip().rstrip("\\").strip() for l in b.split(fmt, 1)[1].split('> "$r/etc/default/opos-osk"')[0].splitlines() if l.strip()]
        self.assertEqual(len(args), 4)
        for a in args:
            self.assertTrue(a.startswith("'#"), a)

    def test_verify_image_checks_the_keyboard_offline(self):
        b = self.block("verify-image.sh", "# --- track osk: the on-screen keyboard", "umount \"$mnt\"")
        for s in ("osk_static/osk.js", "osk_static/osk.css", "Environment=OPOS_OSK=1", "EnvironmentFile=-/etc/default/opos-osk",
                  "sh -n $r/usr/libexec/opos/opos-osk", "graphical.target.wants/opos-osk.service", "Requisite=opos.service",
                  "phosh-osk-stevia"):
            self.assertIn(s, b, s)
        t = (DUO / "verify-image.sh").read_text(encoding="utf-8")
        c = self.block("verify-image.sh", "# --- track osk: the image's own python3", 'kill "$(cat "$work/opos-verify.pid")"')
        self.assertIn("osk.asset('/osk.js')", c)
        self.assertIn("os.environ['OPOS_OSK'] = '1'", c)
        self.assertLess(t.index("# --- track osk: the image's own python3"), t.index("VERIFY: PASS"))

    def test_no_secret_or_key_in_the_keyboard_files(self):
        for p in ("opos-osk", "opos-osk.service", "OSK.md"):
            self.assertNotRegex((DUO / p).read_text(encoding="utf-8"), r"(?i)ssh-ed25519|BEGIN [A-Z ]*PRIVATE|password\s*=")


class DocsTest(unittest.TestCase):
    def test_readme_has_a_short_honest_section(self):
        t = (DUO / "README.md").read_text(encoding="utf-8")
        self.assertEqual(t.count("## On-screen keyboard"), 1)
        s = t.split("## On-screen keyboard", 1)[1].split("\n## ", 1)[0]
        for w in ("OPOS_OSK=1", "/etc/default/opos-osk", "stevia", "split", "OSK.md", "not yet on the phone", "never started"):
            self.assertIn(w, s, w)
        self.assertLess(len(s.split()), 260, "short")

    def test_osk_md_has_the_decision_the_evidence_and_the_live_checks(self):
        t = (DUO / "OSK.md").read_text(encoding="utf-8")
        self.assertTrue(t.startswith("<!-- SPDX-License-Identifier: GPL-3.0-or-later -->\n"))
        for h in ("## The problem", "## What the sources say", "## Decision", "## Option B", "## Option A", "## Live checks for the phone"):
            self.assertIn(h, t, h)
        for w in ("squeekboard", "input-method-relay.c", "phoc_server_filter_globals", "pos_wayland_has_wl_protcols",
                  "not yet on the phone", "Join", "42 px hinge", "OPOS_OSK_HINGE", "G_MESSAGES_DEBUG=all", "POS_DEBUG=force-show",
                  "WAYLAND_DISPLAY", "screen-keyboard-enabled", "active: 1, enabled: 1", "systemctl restart opos-server opos-osk"):
            self.assertIn(w, t, w)
        for w in ("opos-osk.service", "/usr/libexec/opos/opos-osk", "src/opos/osk.py", "src/opos/osk_static/"):
            self.assertIn(w, t, w)

    def test_the_files_the_docs_name_exist(self):
        for p in ("src/opos/osk.py", "src/opos/osk_static/osk.js", "src/opos/osk_static/osk.css", "tests/osk_core_test.js",
                  "packaging/duo-os/opos-osk", "packaging/duo-os/opos-osk.service", "packaging/duo-os/OSK.md"):
            self.assertTrue((ROOT / p).is_file(), p)

    def test_scripts_are_executable_in_the_repo(self):
        if SH is None:
            self.skipTest("no git mode check without a shell")
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "packaging/duo-os/opos-osk"], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            self.skipTest("not committed yet")
        self.assertTrue(r.stdout.startswith("100755"), r.stdout)


if __name__ == "__main__":
    unittest.main()
