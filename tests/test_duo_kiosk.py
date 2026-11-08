# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo OS kiosk script (packaging/duo-os/opos-kiosk) run under sh with a fake chromium, logger, date,
sleep, python3 and pgrep on PATH: lock clearing, WAYLAND_SOCKET, the DevTools port, the back-off and the
no-GPU retry. The fake chromium reads one line of a plan per start ("status seconds") and, when the plan
is spent, ends the script with SIGTERM. The give-up after ten quick exits (phoc ended, exit 1) and the unit settings
that let it reach opos-fallback.service; with OPOS_TEST_SYSTEMD=1 as root, also a scaled model of that chain on the
real systemd. Also the build-image.sh and verify-image.sh parts that go with it."""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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
    "pkill": '#!/bin/sh\necho "$*" >> "$T/pkill.log"\nrc=0\n[ -r "$T/pkill_rc" ] && read -r rc < "$T/pkill_rc"\nexit "$rc"\n',
    "python3": r"""#!/bin/sh
f=0
[ -r "$T/py_fails" ] && read -r f < "$T/py_fails"
[ "$f" -gt 0 ] || exit 0
echo $((f - 1)) > "$T/py_fails"
exit 1
""",
}


def unit_seconds(text):
    """A systemd time span as the unit files write it: 60, 60s, 30min, 1h."""
    m = re.fullmatch(r"(\d+)(s|sec|min|m|h)?", text)
    assert m, text
    return int(m.group(1)) * {None: 1, "s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600}[m.group(2)]


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

    def run_kiosk(self, plan, env=None, defaults=None, returncode=-signal.SIGTERM):
        (self.t / "plan").write_text("".join("%s %s\n" % p for p in plan))
        e = {"PATH": "%s:/usr/bin:/bin" % self.bin, "T": str(self.t), "HOME": str(self.home),
             "OPOS_KIOSK_DEFAULTS": str(defaults or self.t / "no-such-defaults")}
        e.update(env or {})
        r = subprocess.run(["sh", str(KIOSK)], env=e, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, returncode, r.stderr)  # by default: ended by the fake chromium, as planned
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

    def phoc_signals(self):
        try:
            return (self.t / "pkill.log").read_text().splitlines()
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

    # Giving up: a Chromium that never starts (an unwritable profile, a broken install) must end the session, because
    # only a session that ends can reach opos.service's start limit and, with it, OnFailure= (phosh).
    def test_ten_quick_exits_in_a_row_end_phoc_and_the_script_with_status_1(self):
        r = self.run_kiosk([(1, 1)] * 10 + [(0, 100)], returncode=1)
        self.assertEqual(len(self.runs()), 10, "no eleventh start")
        self.assertEqual(self.phoc_signals(), ["-TERM -u %d -x phoc" % os.getuid()])
        self.assertEqual(self.sleeps(), [2, 2, 4, 8, 16, 32, 60, 60, 60], "no pause after the last quick exit")
        self.assertGreaterEqual(sum(self.sleeps()), 120, "the pauses before giving up span at least two minutes")
        gave_up = [m for m in self.logged() if "giving up" in m]
        self.assertEqual(len(gave_up), 1)
        self.assertIn("10 times in a row within 20s (last status 1)", gave_up[0])
        self.assertTrue(gave_up[0].startswith("-t opos-kiosk "))
        self.assertEqual(r.stdout, "")

    def test_nine_quick_exits_do_not_give_up_and_a_long_run_resets_the_count(self):
        self.run_kiosk([(1, 1)] * 9)
        self.assertEqual(self.phoc_signals(), [])
        self.assertFalse(any("giving up" in m for m in self.logged()))
        for f in ("count", "chromium.log", "sleeps"):
            (self.t / f).unlink()
        self.run_kiosk([(1, 1)] * 9 + [(0, 40)] + [(1, 1)] * 9)
        self.assertEqual(len(self.runs()), 19)
        self.assertEqual(self.phoc_signals(), [])

    def test_giving_up_ends_the_script_even_if_pkill_finds_no_phoc_or_is_not_there(self):
        for rc in (1, 127):  # no process matched; command not found
            with self.subTest(pkill_status=rc):
                for f in ("count", "chromium.log", "sleeps", "pkill.log"):
                    (self.t / f).unlink(missing_ok=True)
                (self.t / "pkill_rc").write_text("%d\n" % rc)
                self.run_kiosk([(1, 1)] * 10, returncode=1)
                self.assertEqual(len(self.runs()), 10)
                self.assertEqual(len(self.phoc_signals()), 1)

    def test_give_up_counts_every_kind_of_quick_exit(self):
        # killed by a signal (139), a clean 0 and a plain failure before 20 s are all a Chromium that cannot run
        self.run_kiosk([(139, 1), (0, 1), (2, 1)] * 3 + [(139, 1)], returncode=1)
        self.assertEqual(len(self.runs()), 10)
        self.assertEqual(len(self.phoc_signals()), 1)

    def test_the_start_limit_window_spans_several_whole_failed_sessions(self):
        """opos.service restarts a session that ended; only the start limit stops that. The window must hold the first
        start and the three that follow, with the kiosk's own give-up time, the wait for the server (up to 120 s) and the
        restart's item-composer-fresh (20-40 s) in every session: otherwise the 4th start finds the 1st outside the
        window and the fallback is never reached."""
        self.run_kiosk([(1, 19)] * 10, returncode=1)  # the slowest quick exits: 19 s each
        kiosk_s = sum(self.sleeps()) + 10 * 19
        wait = int(re.search(r'"\$n" -ge (\d+)', KIOSK.read_text()).group(1))
        composer_fresh = 40
        session = composer_fresh + wait + kiosk_s
        unit = (DUO / "opos.service").read_text()
        interval = unit_seconds(re.search(r"(?m)^StartLimitIntervalSec=(\S+)$", unit).group(1))
        burst = int(re.search(r"(?m)^StartLimitBurst=(\d+)$", unit).group(1))
        self.assertEqual(burst, 3)
        self.assertLess(burst * session, interval, "%d s per session x %d must fit in %d s" % (session, burst, interval))
        self.assertGreater(interval, 60, "the old 60 s window could never hold more than one of these sessions")

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
        # Other blocks (the clock path unit) test the same variable, so take the one that writes the debug port.
        i = text.rindex('if [ -n "${OPOS_DEV_SSH_PUBKEY:-}" ]; then', 0, text.index("OPOS_KIOSK_DEBUG_PORT=9222"))
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

    def test_verify_image_checks_the_give_up_and_the_start_limit_against_the_shipped_files(self):
        v = (DUO / "verify-image.sh").read_text()
        line = next(ln for ln in v.splitlines() if ln.startswith('check "a Chromium that never starts ends the session'))
        cond = line.split('" "', 1)[1].rstrip('"')
        # run the check's own condition against the shipped kiosk and unit
        with tempfile.TemporaryDirectory() as d:
            w = Path(d) / "etc" / "systemd" / "system"
            w.mkdir(parents=True)
            shutil.copy(DUO / "opos.service", w / "opos.service")
            script = "k=%s\nw=%s\n%s\n" % (KIOSK, w, cond)
            ok = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            (w / "opos.service").write_text((DUO / "opos.service").read_text().replace("StartLimitIntervalSec=30min", "StartLimitIntervalSec=60"))
            self.assertNotEqual(subprocess.run(["sh", "-c", script], capture_output=True, text=True).returncode, 0, "the old 60 s window must fail the check")

    def test_power_note_exists_and_the_readme_points_to_it(self):
        power = (DUO / "POWER.md").read_text()
        self.assertTrue(power.startswith("<!-- SPDX-License-Identifier: GPL-3.0-or-later -->"))
        self.assertIn("nothing in this note is implemented", power)
        self.assertIn("[POWER.md](POWER.md)", (DUO / "README.md").read_text())

    def test_the_unit_keeps_the_settings_the_fallback_depends_on(self):
        u = (DUO / "opos.service").read_text()
        for s in ("RestartMode=direct", "OnFailure=opos-fallback.service", "StartLimitBurst=3", "ExecStartPre=+chvt 7"):
            self.assertIn(s, u)

    def test_the_unit_restarts_a_session_that_ended_and_its_start_limit_can_be_reached(self):
        u = (DUO / "opos.service").read_text()
        # the kiosk exits 1 after giving up; the unit starts a new session (Restart=always, nothing in RestartPreventExitStatus)
        self.assertRegex(u, r"(?m)^Restart=always$")
        self.assertNotIn("RestartPreventExitStatus", u)
        self.assertNotIn("SuccessExitStatus", u)
        # the limit sits in [Unit], is not switched off, and spans minutes, not the old 60 s
        m = re.search(r"(?m)^StartLimitIntervalSec=(\S+)$", u.split("[Service]")[0])
        self.assertIsNotNone(m, "StartLimitIntervalSec belongs in [Unit]")
        self.assertGreaterEqual(unit_seconds(m.group(1)), 20 * 60)
        self.assertNotRegex(u, r"(?m)^StartLimitAction=")
        # the fallback is what OnFailure= starts; it starts phosh once opos.service has failed
        fb = (DUO / "opos-fallback.service").read_text()
        self.assertIn("systemctl start phosh.service", fb)
        self.assertIn("active|activating|reloading) exit 0", fb)


def systemd_can_be_driven():
    return (os.environ.get("OPOS_TEST_SYSTEMD") == "1" and hasattr(os, "geteuid") and os.geteuid() == 0
            and os.path.isdir("/run/systemd/system") and shutil.which("systemctl") is not None)


@unittest.skipUnless(systemd_can_be_driven(), "set OPOS_TEST_SYSTEMD=1 and run as root on a machine whose PID 1 is systemd (it adds two transient units)")
class SystemdModelTest(unittest.TestCase):
    """The real systemd on a scaled model of opos.service: the restart and start-limit lines are copied from the shipped
    unit, ExecStart runs the real opos-kiosk with a chromium that dies at once (and a sleep that returns at once), and
    OnFailure= starts a stand-in fallback that leaves a marker. It shows that a kiosk that gives up and exits 1 is
    restarted, that the start limit then fails the unit, and that OnFailure= fires. It is not the phone: no phoc, no
    item-composer-fresh, no real durations (the window arithmetic is pinned in KioskScriptTest)."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="opos-model-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.t, True)
        (self.t / "bin").mkdir()
        (self.t / "home").mkdir()
        stubs = {
            "chromium": 'echo run >> "$T/runs"; exit 1',
            "sleep": "exit 0", "python3": "exit 0", "pgrep": "exit 1",
            "logger": 'echo "$*" >> "$T/logger.log"',
            "pkill": 'echo "$*" >> "$T/pkill.log"',
        }
        for name, body in stubs.items():
            p = self.t / "bin" / name
            p.write_text("#!/bin/sh\n" + body + "\n")
            p.chmod(0o755)
        self.name = "opos-model-test-%d" % os.getpid()
        self.addCleanup(self.cleanup_units)

    def systemctl(self, *args):
        return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=60)

    def cleanup_units(self):
        for n in (self.name, self.name + "-fallback"):
            self.systemctl("stop", n + ".service")
            self.systemctl("reset-failed", n + ".service")
            try:
                os.unlink("/run/systemd/system/%s.service" % n)
            except FileNotFoundError:
                pass
        self.systemctl("daemon-reload")

    def shipped(self, key):
        return re.search(r"(?m)^%s=(\S+)$" % key, (DUO / "opos.service").read_text()).group(1)

    def test_a_kiosk_that_gives_up_reaches_the_fallback_through_the_start_limit(self):
        d = Path("/run/systemd/system")
        (d / (self.name + "-fallback.service")).write_text(
            "[Unit]\nDescription=model of opos-fallback.service\n[Service]\nType=oneshot\nExecStart=/bin/sh -c 'date +%%s > %s/fallback-ran'\n" % self.t)
        (d / (self.name + ".service")).write_text(
            "[Unit]\nDescription=scaled model of opos.service\nOnFailure=%s-fallback.service\nStartLimitIntervalSec=%s\nStartLimitBurst=%s\n"
            "[Service]\nEnvironment=T=%s HOME=%s/home PATH=%s/bin:/usr/bin:/bin\nExecStartPre=/bin/sleep 1\nExecStart=/bin/sh %s\n"
            "Restart=%s\nRestartSec=%s\nRestartMode=%s\n" % (
                self.name, self.shipped("StartLimitIntervalSec"), self.shipped("StartLimitBurst"), self.t, self.t, self.t, KIOSK,
                self.shipped("Restart"), self.shipped("RestartSec"), self.shipped("RestartMode")))
        self.assertEqual(self.systemctl("daemon-reload").returncode, 0)
        self.systemctl("start", "--no-block", self.name + ".service")
        marker = self.t / "fallback-ran"
        for _ in range(90):
            if marker.exists():
                break
            time.sleep(1)
        self.assertTrue(marker.exists(), "OnFailure= never started the fallback: " + self.systemctl("status", self.name + ".service", "--no-pager").stdout)
        shown = dict(ln.split("=", 1) for ln in self.systemctl("show", self.name + ".service", "-p", "ActiveState,NRestarts").stdout.splitlines())
        self.assertEqual(shown["ActiveState"], "failed")
        burst = int(self.shipped("StartLimitBurst"))
        self.assertEqual(len((self.t / "pkill.log").read_text().splitlines()), burst, "one give-up per allowed start")
        self.assertEqual(len((self.t / "runs").read_text().splitlines()), burst * 10)


if __name__ == "__main__":
    unittest.main()
