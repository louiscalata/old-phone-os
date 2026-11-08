# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo OS kiosk script (packaging/duo-os/opos-kiosk) run under sh with a fake chromium, logger, date,
sleep, python3 and pgrep on PATH: lock clearing, WAYLAND_SOCKET, the DevTools port, the back-off and the
no-GPU retry. The fake chromium reads one line of a plan per start ("status seconds") and, when the plan
is spent, ends the script with SIGTERM. Also the build-image.sh and verify-image.sh parts that go with it."""
import os
import re
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging" / "duo-os"
KIOSK = DUO / "opos-kiosk"

FAKES = {
    "chromium": r"""#!/bin/sh
n=0
[ -r "$T/count" ] && read -r n < "$T/count"
n=$((n + 1))
line=$(sed -n "${n}p" "$T/plan")
[ -n "$line" ] || { kill -TERM "$PPID"; exit 0; }
echo "$n" > "$T/count"
{
    printf 'RUN\t%s\n' "$n"
    for a in "$@"; do printf 'ARG\t%s\n' "$a"; done
    printf 'WAYLAND_SOCKET\t%s\n' "${WAYLAND_SOCKET-unset}"
    for f in SingletonLock SingletonCookie SingletonSocket Preferences; do
        if [ -e "$HOME/.cache/opos-kiosk/$f" ] || [ -L "$HOME/.cache/opos-kiosk/$f" ]; then printf 'HAS\t%s\n' "$f"; fi
    done
} >> "$T/chromium.log"
read -r clock < "$T/clock"
set -- $line
echo $((clock + $2)) > "$T/clock"
exit "$1"
""",
    "date": '#!/bin/sh\nread -r c < "$T/clock"\necho "$c"\n',
    "sleep": '#!/bin/sh\necho "$1" >> "$T/sleeps"\n',
    "logger": '#!/bin/sh\necho "$*" >> "$T/logger.log"\nrc=0\n[ -r "$T/logger_rc" ] && read -r rc < "$T/logger_rc"\nexit "$rc"\n',
    "pgrep": '#!/bin/sh\n[ -e "$T/busy" ]\n',
    "python3": r"""#!/bin/sh
f=0
[ -r "$T/py_fails" ] && read -r f < "$T/py_fails"
[ "$f" -gt 0 ] || exit 0
echo $((f - 1)) > "$T/py_fails"
exit 1
""",
}


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class KioskScriptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        self.home = self.t / "home"
        self.profile = self.home / ".cache" / "opos-kiosk"
        self.profile.mkdir(parents=True)
        for name, body in FAKES.items():
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        (self.t / "clock").write_text("1000\n")
        (self.t / "py_fails").write_text("0\n")

    def run_kiosk(self, plan, env=None, defaults=None):
        (self.t / "plan").write_text("".join("%s %s\n" % p for p in plan))
        e = {"PATH": "%s:/usr/bin:/bin" % self.bin, "T": str(self.t), "HOME": str(self.home),
             "OPOS_KIOSK_DEFAULTS": str(defaults or self.t / "no-such-defaults")}
        e.update(env or {})
        r = subprocess.run(["sh", str(KIOSK)], env=e, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, -signal.SIGTERM, r.stderr)  # ended by the fake chromium, as planned
        return r

    def runs(self):
        out = []
        try:
            text = (self.t / "chromium.log").read_text()
        except FileNotFoundError:
            return out
        for line in text.splitlines():
            k, v = line.split("\t", 1)
            if k == "RUN":
                out.append({"args": [], "has": [], "wayland": None})
            elif k == "ARG":
                out[-1]["args"].append(v)
            elif k == "HAS":
                out[-1]["has"].append(v)
            else:
                out[-1]["wayland"] = v
        return out

    def sleeps(self):
        try:
            return [int(x) for x in (self.t / "sleeps").read_text().split()]
        except FileNotFoundError:
            return []

    def logged(self):
        try:
            return (self.t / "logger.log").read_text().splitlines()
        except FileNotFoundError:
            return []

    def stale_lock(self):
        (self.profile / "SingletonLock").symlink_to("otherhost-4242")
        (self.profile / "SingletonCookie").symlink_to("1234567890")
        (self.profile / "SingletonSocket").symlink_to("/tmp/.org.chromium.Chromium.gone/SingletonSocket")
        (self.profile / "Preferences").write_text("{}")

    def test_stale_profile_lock_is_removed_and_the_profile_kept(self):
        self.stale_lock()
        self.run_kiosk([(0, 100)])
        self.assertEqual(self.runs()[0]["has"], ["Preferences"])
        self.assertFalse(any(os.path.lexists(self.profile / n) for n in ("SingletonLock", "SingletonCookie", "SingletonSocket")))

    def test_lock_stays_while_a_chromium_still_runs_on_the_profile(self):
        self.stale_lock()
        (self.t / "busy").write_text("")
        self.run_kiosk([(0, 100)])
        self.assertEqual(sorted(self.runs()[0]["has"]), ["Preferences", "SingletonCookie", "SingletonLock", "SingletonSocket"])
        self.assertTrue(any("still running" in m for m in self.logged()))

    def test_chromium_gets_the_kiosk_flags_and_the_profile(self):
        self.run_kiosk([(0, 100)])
        args = self.runs()[0]["args"]
        for a in ("--kiosk", "--ozone-platform=wayland", "--user-data-dir=%s" % self.profile, "http://127.0.0.1:8765/"):
            self.assertIn(a, args)
        self.assertEqual(args[-1], "http://127.0.0.1:8765/")
        self.assertFalse([a for a in args if "remote-debugging" in a or a == "--disable-gpu"])

    def test_wayland_socket_is_not_handed_to_chromium(self):
        self.run_kiosk([(0, 100), (0, 100)], env={"WAYLAND_SOCKET": "3", "WAYLAND_DISPLAY": "wayland-0"})
        self.assertEqual([r["wayland"] for r in self.runs()], ["unset", "unset"])

    def test_debug_port_comes_from_the_defaults_file_only(self):
        d = self.t / "opos-kiosk-defaults"
        d.write_text("# dev\nOPOS_KIOSK_DEBUG_PORT=9222\n")
        self.run_kiosk([(0, 100)], defaults=d)
        args = self.runs()[0]["args"]
        self.assertIn("--remote-debugging-port=9222", args)
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertEqual(args[-1], "http://127.0.0.1:8765/")

    def test_debug_port_in_the_environment_does_not_open_it(self):
        # e.g. an Environment= line in a unit: only /etc/default/opos-kiosk may enable the port
        for defaults in (None, "no-port-in-it"):
            with self.subTest(defaults=defaults):
                d = None
                if defaults:
                    d = self.t / "opos-kiosk-defaults"
                    d.write_text("# nothing about the port here\n")
                (self.t / "chromium.log").unlink(missing_ok=True)
                (self.t / "count").unlink(missing_ok=True)
                self.run_kiosk([(0, 100)], env={"OPOS_KIOSK_DEBUG_PORT": "9333"}, defaults=d)
                self.assertFalse([a for a in self.runs()[0]["args"] if "remote-debugging" in a])
                self.assertFalse(any("not a port number" in m for m in self.logged()))

    def test_debug_port_in_the_environment_is_not_used_when_the_defaults_file_is_broken(self):
        d = self.t / "opos-kiosk-defaults"
        d.write_text("OPOS_KIOSK_DEBUG_PORT=9222\nif then fi (\n")
        self.run_kiosk([(0, 100)], env={"OPOS_KIOSK_DEBUG_PORT": "9333"}, defaults=d)
        self.assertFalse([a for a in self.runs()[0]["args"] if "remote-debugging" in a])

    def test_the_defaults_file_decides_the_port_whatever_the_environment_says(self):
        d = self.t / "opos-kiosk-defaults"
        d.write_text("OPOS_KIOSK_DEBUG_PORT=9222\n")
        self.run_kiosk([(0, 100)], env={"OPOS_KIOSK_DEBUG_PORT": "9333"}, defaults=d)
        args = self.runs()[0]["args"]
        self.assertIn("--remote-debugging-port=9222", args)
        self.assertNotIn("--remote-debugging-port=9333", args)

    def test_bad_debug_port_is_logged_and_ignored(self):
        d = self.t / "opos-kiosk-defaults"
        d.write_text("OPOS_KIOSK_DEBUG_PORT='9222; reboot'\n")
        self.run_kiosk([(0, 100)], defaults=d)
        self.assertFalse([a for a in self.runs()[0]["args"] if "remote-debugging" in a])
        self.assertTrue(any("not a port number" in m for m in self.logged()))

    def test_broken_defaults_file_cannot_end_the_kiosk(self):
        d = self.t / "opos-kiosk-defaults"
        d.write_text("OPOS_KIOSK_DEBUG_PORT=9222\nif then fi (\n")
        self.run_kiosk([(0, 100)], defaults=d)
        self.assertEqual(len(self.runs()), 1)
        self.assertFalse([a for a in self.runs()[0]["args"] if "remote-debugging" in a])

    def test_quick_exits_back_off_and_drop_the_gpu_from_the_third(self):
        self.run_kiosk([(1, 1)] * 9)
        runs = self.runs()
        self.assertEqual(len(runs), 9)
        gpu = ["--disable-gpu" in r["args"] for r in runs]
        self.assertEqual(gpu, [False, False, False] + [True] * 6)
        self.assertEqual(self.sleeps(), [2, 2, 4, 8, 16, 32, 60, 60, 60])
        log = self.logged()
        self.assertEqual(sum("--disable-gpu" in m for m in log), 1)  # said once, at the switch
        self.assertEqual(sum("chromium exited with status 1 after 1s" in m for m in log), 9)
        self.assertIn("-t opos-kiosk chromium exited with status 1 after 1s (quick exits in a row: 3); starting it again in 4s", log)

    def test_a_long_run_resets_the_count_and_the_pause(self):
        self.run_kiosk([(1, 1), (1, 1), (0, 40), (1, 1), (1, 1), (0, 40)])
        self.assertFalse(any("--disable-gpu" in r["args"] for r in self.runs()))
        self.assertEqual(self.sleeps(), [2] * 6)

    def test_the_gpu_stays_off_after_a_long_run_but_the_pause_resets(self):
        self.run_kiosk([(1, 1)] * 3 + [(0, 40), (1, 1)])
        self.assertEqual(["--disable-gpu" in r["args"] for r in self.runs()], [False] * 3 + [True] * 2)
        self.assertEqual(self.sleeps(), [2, 2, 4, 2, 2])

    def test_a_clock_that_goes_back_is_not_a_quick_exit(self):
        self.run_kiosk([(1, -500)] * 4)
        self.assertFalse(any("--disable-gpu" in r["args"] for r in self.runs()))
        self.assertEqual(self.sleeps(), [2] * 4)

    def test_waits_for_the_server_then_goes_on(self):
        (self.t / "py_fails").write_text("3\n")
        self.run_kiosk([(0, 100)])
        self.assertEqual(self.sleeps()[:4], [1, 1, 1, 2])
        self.assertFalse(any("does not answer" in m for m in self.logged()))

    def test_a_server_that_never_answers_is_logged_and_chromium_started_anyway(self):
        (self.t / "py_fails").write_text("100000\n")
        self.run_kiosk([(0, 100)])
        self.assertEqual(self.sleeps()[:119], [1] * 119)
        self.assertEqual(len(self.runs()), 1)
        self.assertTrue(any("does not answer after 120 tries" in m for m in self.logged()))

    def test_a_failing_logger_falls_back_to_stderr(self):
        (self.t / "logger_rc").write_text("1\n")
        r = self.run_kiosk([(3, 1)])
        self.assertIn("opos-kiosk: chromium exited with status 3 after 1s", r.stderr)

    def test_every_exit_is_logged_with_the_opos_kiosk_tag(self):
        self.run_kiosk([(0, 100)])
        self.assertTrue(all(m.startswith("-t opos-kiosk ") for m in self.logged()))
        self.assertEqual(len(self.logged()), 1)


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class ImageScriptsTest(unittest.TestCase):
    def test_every_script_parses_and_starts_with_spdx(self):
        for name in ("opos-kiosk", "opos-session", "opos-switch", "opos-diag", "build-image.sh", "verify-image.sh"):
            p = DUO / name
            with self.subTest(name):
                r = subprocess.run(["sh", "-n", str(p)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                head = p.read_text().splitlines()[:2]
                self.assertIn("# SPDX-License-Identifier: GPL-3.0-or-later", head)
                self.assertNotIn(b"\r", p.read_bytes())

    def build_block(self, start, end):
        text = (DUO / "build-image.sh").read_text()
        i = text.index(start)
        j = text.index(end, i) + len(end)
        return text[i:j]

    def edit_watchdog_runs(self, script, times=1):
        """The edit block run `times` times in a row on one copy of the script: [(result, text, mode)] per run."""
        block = self.build_block("wd=$r/usr/local/sbin/sfduo-composer-watchdog", "edit did not take\" >&2; exit 1\nfi\n")
        out = []
        with tempfile.TemporaryDirectory() as r:
            wd = Path(r) / "usr" / "local" / "sbin" / "sfduo-composer-watchdog"
            wd.parent.mkdir(parents=True)
            wd.write_text(script)
            wd.chmod(0o755)
            for _ in range(times):
                res = subprocess.run(["sh", "-eu", "-c", block], env={"r": r, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
                out.append((res, wd.read_text(), oct(wd.stat().st_mode & 0o777)))
        return out

    def edit_watchdog(self, script):
        return self.edit_watchdog_runs(script)[0]

    # The tail of the port's sfduo-composer-watchdog (the part the edit touches), as shipped in the Droidian
    # port's rootfs: a master_ok / composer_pid script that ends in a try-restart of phosh.
    WATCHDOG = ("#!/bin/sh\nCLIENTS=/sys/kernel/debug/dri/0/clients\n"
                "master_ok() {\n    awk '$1 ~ /composer/ && $4 == \"y\" {found=1} END {exit !found}' \"$CLIENTS\" 2>/dev/null\n}\n"
                "if master_ok; then\n    echo \"composer reacquired DRM master - restarting phosh\"\n"
                "    systemctl try-restart phosh.service 2>/dev/null || true\n    exit 0\nfi\n"
                "echo \"composer still has no DRM master - manual attention needed\"\nexit 1\n")

    def test_watchdog_edit_restarts_opos_too(self):
        script = ("#!/bin/sh\nif master_ok; then\n    echo \"composer reacquired DRM master - restarting phosh\"\n"
                  "    systemctl try-restart phosh.service 2>/dev/null || true\n    exit 0\nfi\nexit 1\n")
        res, new, mode = self.edit_watchdog(script)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("    systemctl try-restart phosh.service opos.service 2>/dev/null || true\n", new)
        self.assertIn("restarting the shell (phosh or opos)\"", new)
        self.assertEqual(mode, "0o755")
        self.assertEqual(new.count("try-restart"), 1)

    def test_watchdog_edit_run_twice_changes_nothing_the_second_time(self):
        # an incremental build uses an earlier OPOS image as BASE: the script is already edited
        (first, once, _), (second, twice, mode) = self.edit_watchdog_runs(self.WATCHDOG, times=2)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(once, self.WATCHDOG)
        self.assertEqual(twice, once)
        self.assertIn("    systemctl try-restart phosh.service opos.service 2>/dev/null || true\n", twice)
        self.assertEqual(twice.count("opos.service"), 1)
        self.assertEqual(twice.count("(phosh or opos)"), 1)
        self.assertEqual(mode, "0o755")

    def test_watchdog_edit_accepts_a_script_that_already_has_it(self):
        edited = self.WATCHDOG.replace("try-restart phosh.service ", "try-restart phosh.service opos.service ")
        res, new, _ = self.edit_watchdog(edited)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(new, edited)
        self.assertNotIn("not the script", res.stderr)

    def test_watchdog_edit_refuses_a_script_it_was_not_written_for(self):
        res, new, _ = self.edit_watchdog("#!/bin/sh\nsystemctl restart phosh.service\n")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("not the script the OPOS edit is written for", res.stderr)
        self.assertEqual(new, "#!/bin/sh\nsystemctl restart phosh.service\n")

    def test_debug_port_file_is_written_only_for_a_dev_image(self):
        text = (DUO / "build-image.sh").read_text()
        i = text.index('if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then')
        j = text.index("\nfi\n", i)
        dev = text[i:j]
        self.assertEqual(text.count("OPOS_KIOSK_DEBUG_PORT=9222"), 1)
        before, after = dev.split("\nelse\n")
        self.assertIn("OPOS_KIOSK_DEBUG_PORT=9222", before)
        self.assertIn('> "$r/etc/default/opos-kiosk"', before)
        self.assertEqual(after.strip(), 'rm -f "$r/etc/default/opos-kiosk"')
        # the lines that write it, run as written
        m = re.search(r"(    printf '%s\\n'.*?\n    chmod 0644 \"\$r/etc/default/opos-kiosk\"\n)", before, re.S)
        self.assertTrue(m)
        with tempfile.TemporaryDirectory() as r:
            (Path(r) / "etc" / "default").mkdir(parents=True)
            res = subprocess.run(["sh", "-eu", "-c", m.group(1)], env={"r": r, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            f = Path(r) / "etc" / "default" / "opos-kiosk"
            self.assertIn("OPOS_KIOSK_DEBUG_PORT=9222\n", f.read_text())
            self.assertTrue(f.read_text().startswith("# DEV IMAGE"))
            self.assertEqual(oct(f.stat().st_mode & 0o777), "0o644")

    def test_verify_image_checks_what_the_build_adds(self):
        v = (DUO / "verify-image.sh").read_text()
        for s in ("try-restart phosh.service opos.service", "unset WAYLAND_SOCKET", "SingletonLock", "OPOS_KIOSK_DEBUG_PORT=9222", "authorized_keys"):
            self.assertIn(s, v)

    def test_power_note_exists_and_the_readme_points_to_it(self):
        power = (DUO / "POWER.md").read_text()
        self.assertTrue(power.startswith("<!-- SPDX-License-Identifier: GPL-3.0-or-later -->"))
        self.assertIn("nothing in this note is implemented", power)
        self.assertIn("[POWER.md](POWER.md)", (DUO / "README.md").read_text())

    def test_the_unit_keeps_the_settings_the_fallback_depends_on(self):
        u = (DUO / "opos.service").read_text()
        for s in ("RestartMode=direct", "OnFailure=opos-fallback.service", "StartLimitBurst=3", "ExecStartPre=+chvt 7"):
            self.assertIn(s, u)


if __name__ == "__main__":
    unittest.main()
