# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo OS kiosk script (packaging/duo-os/opos-kiosk) run under sh with a fake chromium, logger, date,
sleep, python3 and pgrep on PATH: lock clearing, WAYLAND_SOCKET, the DevTools port, the back-off and the
no-GPU retry. The fake chromium reads one line of a plan per start ("status seconds") and, when the plan
is spent, ends the script with SIGTERM. The give-up (6 quick exits in a row, or 10 exits of any length within 10
minutes: a marker file, then exit 1), opos-session (phoc as its child; on the marker it ends phoc and exits 75) and the
unit settings that carry that status to opos-fallback.service; with OPOS_TEST_SYSTEMD=1 as root, also the whole chain on
the real systemd with a stub phoc, chromium and phosh. Also the build-image.sh and verify-image.sh parts that go with it."""
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
    # a pause moves the fake clock on, so the gaps between exits are the run plus the pause, as on the phone
    "sleep": '#!/bin/sh\necho "$1" >> "$T/sleeps"\nread -r c < "$T/clock"\necho $((c + $1)) > "$T/clock"\n',
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
        self.run_dir = self.t / "run"
        self.run_dir.mkdir()
        self.marker = self.run_dir / "opos-gave-up"

    def run_kiosk(self, plan, env=None, defaults=None, returncode=-signal.SIGTERM):
        (self.t / "plan").write_text("".join("%s %s\n" % p for p in plan))
        e = {"PATH": "%s:/usr/bin:/bin" % self.bin, "T": str(self.t), "HOME": str(self.home), "XDG_RUNTIME_DIR": str(self.run_dir),
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
        self.run_kiosk([(1, 1)] * 5)  # one short of giving up
        runs = self.runs()
        self.assertEqual(len(runs), 5)
        gpu = ["--disable-gpu" in r["args"] for r in runs]
        self.assertEqual(gpu, [False, False, False] + [True] * 2)
        self.assertEqual(self.sleeps(), [2, 2, 4, 8, 16])
        log = self.logged()
        self.assertEqual(sum("--disable-gpu" in m for m in log), 1)  # said once, at the switch
        self.assertEqual(sum("chromium exited with status 1 after 1s" in m for m in log), 5)
        self.assertIn("-t opos-kiosk chromium exited with status 1 after 1s (quick exits in a row: 3); starting it again in 4s", log)

    # Giving up: a Chromium that cannot run (an unwritable profile, a broken install) ends the session with a marker file;
    # opos-session ends phoc and exits 75, which opos.service does not restart (see SessionScriptTest and SystemdModelTest).
    def test_six_quick_exits_in_a_row_leave_the_marker_and_end_the_script_with_status_1(self):
        r = self.run_kiosk([(1, 1)] * 6 + [(0, 100)], returncode=1)
        self.assertEqual(len(self.runs()), 6, "no seventh start")
        self.assertEqual(self.sleeps(), [2, 2, 4, 8, 16], "no pause after the last quick exit")
        self.assertEqual(sum(self.sleeps()), 32, "the pauses before giving up: about half a minute")
        gave_up = [m for m in self.logged() if "giving up" in m]
        self.assertEqual(len(gave_up), 1)
        self.assertIn("6 times in a row within 20s (last status 1)", gave_up[0])
        self.assertTrue(gave_up[0].startswith("-t opos-kiosk "))
        self.assertEqual(self.marker.read_text().strip(), "chromium exited 6 times in a row within 20s (last status 1)")
        self.assertEqual(self.phoc_signals(), [], "phoc is ended by opos-session, which waits for it; the kiosk does not signal it")
        self.assertEqual(r.stdout, "")

    def test_five_quick_exits_do_not_give_up_and_a_long_run_resets_the_count(self):
        self.run_kiosk([(1, 1)] * 5)
        self.assertFalse(self.marker.exists())
        self.assertFalse(any("giving up" in m for m in self.logged()))
        for f in ("count", "chromium.log", "sleeps"):
            (self.t / f).unlink()
        # 5 quick exits, a run of 40 s, 3 more: 8 quick exits in all, but never 6 in a row (and 9 exits are fewer than the 10 of the window)
        self.run_kiosk([(1, 1)] * 5 + [(0, 40)] + [(1, 1)] * 3)
        self.assertEqual(len(self.runs()), 9)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.phoc_signals(), [])

    def test_give_up_counts_every_kind_of_quick_exit(self):
        # killed by a signal (139), a clean 0 and a plain failure before 20 s are all a Chromium that cannot run
        self.run_kiosk([(139, 1), (0, 1), (2, 1)] * 2, returncode=1)
        self.assertEqual(len(self.runs()), 6)
        self.assertTrue(self.marker.exists())

    def test_a_stale_marker_is_overwritten_not_trusted(self):
        self.marker.write_text("an earlier session\n")
        self.run_kiosk([(1, 1)] * 6, returncode=1)
        self.assertIn("6 times in a row", self.marker.read_text())
        self.assertNotIn("earlier", self.marker.read_text())

    def test_no_marker_without_a_give_up(self):
        self.run_kiosk([(0, 100)] * 3)
        self.assertFalse(self.marker.exists())

    def test_a_marker_that_cannot_be_written_falls_back_to_asking_phoc_to_end(self):
        # the runtime directory is gone, or the marker's name is taken by a directory (a failure that root cannot skip, too)
        for how in ("no directory", "a directory in the way"):
            with self.subTest(how):
                for f in ("count", "chromium.log", "sleeps", "pkill.log", "logger.log"):
                    (self.t / f).unlink(missing_ok=True)
                shutil.rmtree(self.marker, ignore_errors=True)
                env = {}
                if how == "no directory":
                    env["XDG_RUNTIME_DIR"] = str(self.t / "gone")
                else:
                    self.marker.mkdir()
                r = self.run_kiosk([(1, 1)] * 6, env=env, returncode=1)
                self.assertEqual(self.phoc_signals(), ["-TERM -u %d -x phoc" % os.getuid()])
                self.assertTrue(any("cannot write" in m and "asking phoc to end" in m for m in self.logged()), self.logged())
                self.assertEqual(r.stderr.strip(), "", "a failing redirection must not leak the shell's own error text")

    def test_the_fallback_for_an_unwritable_marker_ends_the_script_even_if_pkill_finds_no_phoc_or_is_not_there(self):
        for rc in (1, 127):  # no process matched; command not found
            with self.subTest(pkill_status=rc):
                for f in ("count", "chromium.log", "sleeps", "pkill.log"):
                    (self.t / f).unlink(missing_ok=True)
                (self.t / "pkill_rc").write_text("%d\n" % rc)
                self.run_kiosk([(1, 1)] * 6, env={"XDG_RUNTIME_DIR": str(self.t / "gone")}, returncode=1)
                self.assertEqual(len(self.runs()), 6)
                self.assertEqual(len(self.phoc_signals()), 1)

    # The rolling rule: a Chromium that crashes every 20-60 s never has a "quick" exit, so the count in a row never grows.
    def test_ten_exits_within_ten_minutes_give_up_however_long_each_ran(self):
        r = self.run_kiosk([(1, 25)] * 10 + [(0, 100)], returncode=1)  # 25 s: not quick, so the count in a row stays at 0
        self.assertEqual(len(self.runs()), 10, "no eleventh start")
        self.assertEqual(self.sleeps(), [2] * 9, "a run that long resets the pause: no back-off")
        self.assertFalse(any("--disable-gpu" in run["args"] for run in self.runs()), "no quick exit, so the GPU stays on")
        gave_up = [m for m in self.logged() if "giving up" in m]
        self.assertEqual(len(gave_up), 1)
        self.assertIn("10 times within 600s, however long each ran (last status 1, last run 25s)", gave_up[0])
        self.assertIn("10 times within 600s", self.marker.read_text())
        self.assertEqual(self.phoc_signals(), [])
        self.assertEqual(r.stdout, "")

    def test_nine_slow_exits_do_not_give_up(self):
        self.run_kiosk([(1, 25)] * 9)
        self.assertFalse(self.marker.exists())

    def test_the_slowest_crash_loop_the_window_catches_is_about_one_exit_a_minute(self):
        # 10 exits span 9 gaps, and a gap is the run plus the 2 s pause: 9 x (64 + 2) = 594 s fits in 600, 9 x (65 + 2) = 603 does not
        self.run_kiosk([(1, 64)] * 10, returncode=1)
        self.assertTrue(self.marker.exists())
        for f in ("count", "chromium.log", "sleeps", "logger.log"):
            (self.t / f).unlink()
        self.marker.unlink()
        self.run_kiosk([(1, 65)] * 14)  # every window of ten exits is wider than ten minutes
        self.assertFalse(self.marker.exists())
        self.assertEqual(len(self.runs()), 14)

    def test_exits_spread_over_more_than_ten_minutes_do_not_give_up(self):
        # a few early exits, a long quiet stretch (that run's own exit counts, too), then a few more: the early ones leave the window
        self.run_kiosk([(1, 25)] * 5 + [(0, 700)] + [(1, 25)] * 5)
        self.assertFalse(self.marker.exists())
        self.assertEqual(len(self.runs()), 11)

    def test_a_clock_that_goes_back_never_fills_the_window(self):
        self.run_kiosk([(1, -500)] * 14)
        self.assertFalse(self.marker.exists())
        self.assertEqual(len(self.runs()), 14)

    def test_the_window_slides_over_the_last_ten_exits(self):
        # nine slow-ish runs, then fast ones: ten consecutive exits first fit in 600 s somewhere after the tenth exit
        plan = [(1, 84)] * 9 + [(1, 25)] * 9
        end, expect = 1000, None
        exits = []
        for i, (_, secs) in enumerate(plan, 1):
            end += secs
            exits.append(end)
            end += 2  # the pause after a run that was not quick
            if len(exits) >= 10 and exits[-1] - exits[-10] <= 600:
                expect = i
                break
        self.assertGreater(expect, 10, "the oracle: the first ten exits are too far apart, a later ten are not")
        self.run_kiosk(plan, returncode=1)
        self.assertEqual(len(self.runs()), expect)
        self.assertTrue(self.marker.exists())

    def test_the_owner_waits_about_three_minutes_at_most_with_a_chromium_that_cannot_start(self):
        """Time from the unit's start to its failure when Chromium cannot run, each cost modelled on its own, worst case first.
        The unit's start-limit window plays no part (RestartPreventExitStatus=75 fails the unit at once), so there is no
        window arithmetic here. Not modelled: the wait for the OPOS server, which comes first (see below), and what follows
        the failure (the fallback's 2 s, a second item-composer-fresh, phosh starting)."""
        unit = (DUO / "opos.service").read_text()
        timeout_start = unit_seconds(re.search(r"(?m)^TimeoutStartSec=(\S+)$", unit).group(1))
        composer_fresh_estimate = 40  # s, 20-40 in the build notes: ExecStartPre= is cut off at TimeoutStartSec
        pre = min(composer_fresh_estimate, timeout_start)
        self.assertEqual(pre, timeout_start, "the estimate exceeds the cap, so the cap is what a start can cost")
        session = (DUO / "opos-session").read_text()
        poll = 1  # the session looks for the marker every second (sleep 1)
        self.assertIn("until [ -e \"$gave_up\" ]; do sleep 1; done", session)
        grace = int(re.search(r"(?m)^grace=(\d+)$", session).group(1))
        server_up = 1.0  # the wait for the server: the first try answers; one python3 start, about half a second on the phone
        for name, run_s, exits in (("Chromium dies at once", 0, 6), ("slowest quick exits, 19 s each", 19, 6)):
            with self.subTest(name):
                for f in ("count", "chromium.log", "sleeps", "logger.log"):
                    (self.t / f).unlink(missing_ok=True)
                self.run_kiosk([(1, run_s)] * exits, returncode=1)
                total = pre + server_up + sum(self.sleeps()) + exits * run_s + poll + grace
                if run_s == 0:
                    self.assertLessEqual(total, 80, "about a minute when Chromium dies at once")
                else:
                    self.assertLessEqual(total, 190, "about three minutes at the very worst, %.0f s" % total)
                    self.assertGreater(total, 150, "the model is near the worst case, not a different case")
        # With the OPOS server dead as well, the kiosk first waits: up to 120 tries of a python3 start (about 0.5 s) and a
        # sleep of 1 s, 180 s, and 360 s if the server accepts and hangs (2 s timeout per try). Chromium is started anyway
        # afterwards. That wait is separate and is not part of "2-3 minutes"; the README says so.
        tries = int(re.search(r'"\$n" -ge (\d+)', KIOSK.read_text()).group(1))
        self.assertEqual(tries, 120)
        self.assertLessEqual(tries * (1 + 0.5), 180)
        self.assertLessEqual(tries * (1 + 2), 360)

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


SESSION = DUO / "opos-session"

# A stand-in for phoc, shared by the session tests and the systemd model. It must be called "phoc": `pkill -x phoc` matches the
# process name, and a script's name is its name. $T/phoc_mode says how it behaves; -E CMD is its client, as for the real phoc.
PHOC_STUB = r"""#!/bin/sh
echo $$ > "$T/phoc.pid"
stdin=$(readlink /proc/$$/fd/0)
{
    echo "stdin=$stdin"
    for f in /proc/$$/fd/*; do [ "$(readlink "$f")" = "$stdin" ] && echo "fd=${f##*/}"; done
    echo "WLR_BACKENDS=$WLR_BACKENDS"
    echo "args=$*"
} > "$T/phoc.info"
cmd=
while [ $# -gt 0 ]; do
    case "$1" in -E) cmd=$2; shift 2;; *) shift;; esac
done
read -r mode < "$T/phoc_mode"
case $mode in
exit=*) exit "${mode#exit=}";;
segv) kill -SEGV $$;;
exit-with-client)   # ends when its client ends (what phoc may do)
    "$cmd"
    exit 0;;
ignore-term)        # runs its client, then goes on running; TERM only leaves a line behind
    trap 'echo TERM >> "$T/phoc.sig"' TERM
    "$cmd"
    while :; do /bin/sleep 0.05; done;;
obey-term)          # runs its client, goes on running, ends on TERM
    trap 'echo TERM >> "$T/phoc.sig"; exit 0' TERM
    "$cmd" &
    wait $!
    while :; do /bin/sleep 0.05; done;;
esac
"""


def alive(pid):
    """True while the process exists and is not a zombie that nobody has reaped yet."""
    try:
        state = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def session_copy(dest, phoc, kiosk):
    """The shipped opos-session with its two absolute paths (phoc and the kiosk) pointed at stand-ins; nothing else changes."""
    text = SESSION.read_text()
    assert text.count("/usr/bin/phoc") == 1 and text.count("/usr/libexec/opos/opos-kiosk") == 1
    dest.write_text(text.replace("/usr/bin/phoc", str(phoc)).replace("/usr/libexec/opos/opos-kiosk", str(kiosk)))
    return dest


@unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux (/proc)")
class SessionScriptTest(unittest.TestCase):
    """opos-session run for real with a stub systemd-cat, dbus-run-session and phoc. The stand-in pgrep and pkill act only on the
    stub phoc, so the tests can never signal a real compositor on the machine that runs them."""

    BIN = {
        "systemd-cat": '#!/bin/sh\nshift 2\nexec "$@"\n',  # systemd-cat -t TAG COMMAND: runs COMMAND with its output on the journal
        # a parent that passes no signal on, and hands its stdin to the child (a shell's background job would get /dev/null)
        "dbus-run-session": '#!/bin/sh\necho "$@" >> "$T/dbus.log"\nshift\nexec 4<&0\n"$@" <&4 4<&- &\nwait $!\nexit $?\n',
        "sleep": '#!/bin/sh\nexec /bin/sleep "$(awk "BEGIN { print $1 / 5 }")"\n',  # 5 times faster: the 5 s grace is 1 s, which a session that leaves the kill to somebody later cannot hide
        "pgrep": '#!/bin/sh\nread -r p < "$T/phoc.pid" 2>/dev/null || exit 1\nkill -0 "$p" 2>/dev/null\n',
        "pkill": '#!/bin/sh\necho "$*" >> "$T/pkill.log"\nread -r p < "$T/phoc.pid" 2>/dev/null || exit 1\nkill "$1" "$p" 2>/dev/null\n',
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        for name, body in self.BIN.items():
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        (self.t / "phoc").write_text(PHOC_STUB)
        (self.t / "phoc").chmod(0o755)
        (self.t / "run").mkdir()
        self.marker = self.t / "run" / "opos-gave-up"
        self.addCleanup(self.reap)

    def reap(self):
        try:
            pid = int((self.t / "phoc.pid").read_text())
            os.kill(pid, signal.SIGKILL)
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            pass

    def kiosk(self, gives_up=True):
        body = '#!/bin/sh\n/bin/sleep 0.2\n'
        if gives_up:
            body += 'echo "the stub kiosk gave up" > "$XDG_RUNTIME_DIR/opos-gave-up"\n'
        (self.t / "kiosk").write_text(body + "exit 1\n")
        (self.t / "kiosk").chmod(0o755)

    def run_session(self, mode, gives_up=True, stdin=None, env=None):
        (self.t / "phoc_mode").write_text(mode + "\n")
        self.kiosk(gives_up)
        script = session_copy(self.t / "opos-session", self.t / "phoc", self.t / "kiosk")
        e = {"PATH": "%s:/usr/bin:/bin" % self.bin, "T": str(self.t), "XDG_RUNTIME_DIR": str(self.t / "run")}
        e.update(env or {})
        start = time.monotonic()
        # files, not pipes: a process the session left behind would hold a pipe open and make the read wait for it to be gone
        with open(self.t / "stdout", "w") as out_f, open(self.t / "stderr", "w") as err_f:
            p = subprocess.Popen(["sh", str(script)], env=e, stdin=stdin, stdout=out_f, stderr=err_f, text=True, start_new_session=True)
            self.addCleanup(kill_group, p.pid)  # nothing of a failed run may outlive the test
            p.wait(timeout=60)
        took = time.monotonic() - start
        return subprocess.CompletedProcess(p.args, p.returncode, (self.t / "stdout").read_text(), (self.t / "stderr").read_text()), took

    def info(self):
        return dict(ln.split("=", 1) for ln in (self.t / "phoc.info").read_text().splitlines() if "=" in ln)

    def test_the_phoc_command_line_is_the_shipped_one_and_phoc_is_started_as_before(self):
        r, _ = self.run_session("exit=0")
        self.assertEqual(r.returncode, 0, r.stderr)
        info = self.info()
        ini = "/etc/phosh/phoc.ini" if os.path.exists("/etc/phosh/phoc.ini") else "/usr/share/phosh/phoc.ini"
        self.assertEqual(info["args"], "-v -C %s -E %s" % (ini, self.t / "kiosk"))
        self.assertEqual(info["WLR_BACKENDS"], "hwcomposer,libinput")
        self.assertEqual((self.t / "dbus.log").read_text().strip(), "-- %s -v -C %s -E %s" % (self.t / "phoc", ini, self.t / "kiosk"))

    def test_a_dbus_session_that_exists_is_used_and_no_private_bus_is_started(self):
        r, _ = self.run_session("exit=0", env={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.t / "dbus.log").exists())

    def test_wlr_backends_from_the_unit_is_kept(self):
        self.run_session("exit=0", env={"WLR_BACKENDS": "headless"})
        self.assertEqual(self.info()["WLR_BACKENDS"], "headless")

    def test_phoc_gets_the_units_stdin_not_dev_null_and_no_extra_descriptor(self):
        f = self.t / "stdin-file"
        f.write_text("")
        with open(f) as fh:
            self.run_session("exit=0", stdin=fh)
        info = (self.t / "phoc.info").read_text().splitlines()
        self.assertIn("stdin=%s" % f, info)
        self.assertEqual([ln for ln in info if ln.startswith("fd=")], ["fd=0"], "the saved copy of stdin must not leak into phoc")

    def test_a_phoc_that_ends_by_itself_gives_its_status(self):
        for status in (0, 1, 3, 143):
            with self.subTest(status=status):
                r, _ = self.run_session("exit=%d" % status)
                self.assertEqual(r.returncode, status, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertFalse(self.marker.exists())

    def test_a_phoc_killed_by_a_signal_gives_128_plus_the_signal(self):
        r, _ = self.run_session("segv")
        self.assertEqual(r.returncode, 128 + signal.SIGSEGV)

    def test_75_is_the_give_up_status_alone(self):
        r, _ = self.run_session("exit=75")
        self.assertEqual(r.returncode, 1, "a phoc that happens to exit 75 must not look like the kiosk giving up")
        self.assertFalse(any(ln.startswith("opos-session:") for ln in r.stdout.splitlines()))

    def test_a_stale_marker_from_an_earlier_session_is_removed_at_the_start(self):
        self.marker.write_text("an earlier session gave up\n")
        r, _ = self.run_session("exit=0")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_the_marker_ends_phoc_that_ends_with_its_client_and_the_status_is_75(self):
        r, _ = self.run_session("exit-with-client")
        self.assertEqual(r.returncode, 75, r.stdout + r.stderr)
        self.assertIn("exiting 75", r.stdout)

    def test_a_phoc_that_ignores_term_is_ended_anyway_and_the_status_is_75(self):
        r, took = self.run_session("ignore-term")
        self.assertEqual(r.returncode, 75, r.stdout + r.stderr)
        self.assertLess(took, 20, "the script must not wait for phoc forever")
        self.assertEqual((self.t / "phoc.sig").read_text().split(), ["TERM"], "phoc was asked first (TERM), and then it was killed")
        pid = int((self.t / "phoc.pid").read_text())
        for _ in range(30):  # a killed process needs a moment to go; a session that left phoc to somebody else's later kill (1 s) needs more
            if not alive(pid):
                break
            time.sleep(0.01)
        self.assertFalse(alive(pid), "phoc was still running when the session ended")
        self.assertIn("opos-kiosk gave up (the stub kiosk gave up); ending phoc", r.stdout)
        self.assertIn("phoc ended after opos-kiosk gave up; exiting 75", r.stdout)

    def test_a_phoc_that_ends_on_term_is_ended_and_the_status_is_75(self):
        r, _ = self.run_session("obey-term")
        self.assertEqual(r.returncode, 75, r.stdout + r.stderr)
        self.assertEqual((self.t / "phoc.sig").read_text().split(), ["TERM"])
        self.assertFalse(alive(int((self.t / "phoc.pid").read_text())))

    def test_without_a_give_up_a_running_phoc_is_not_touched(self):
        # the kiosk exits without a marker; phoc is still there afterwards: the session keeps waiting, and a TERM to it ends it
        (self.t / "phoc_mode").write_text("obey-term\n")
        self.kiosk(gives_up=False)
        script = session_copy(self.t / "opos-session", self.t / "phoc", self.t / "kiosk")
        e = {"PATH": "%s:/usr/bin:/bin" % self.bin, "T": str(self.t), "XDG_RUNTIME_DIR": str(self.t / "run")}
        p = subprocess.Popen(["sh", str(script)], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        # systemd stops a unit by signalling every process in its cgroup; here that is the process group
        self.addCleanup(kill_group, p.pid)
        time.sleep(1.5)  # long past the kiosk's exit and many polls of the marker
        self.assertIsNone(p.poll(), "the session ended although nothing gave up")
        self.assertFalse((self.t / "phoc.sig").exists())
        self.assertTrue(alive(int((self.t / "phoc.pid").read_text())))
        os.killpg(p.pid, signal.SIGTERM)
        p.communicate(timeout=20)
        self.assertEqual(p.returncode, -signal.SIGTERM)


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

    def test_verify_image_checks_the_give_up_chain_against_the_shipped_files(self):
        """The check's own condition, run against the shipped files (passes) and against each part taken away (fails)."""
        v = (DUO / "verify-image.sh").read_text()
        line = next(ln for ln in v.splitlines() if ln.startswith('check "a Chromium that cannot run ends the session'))
        cond = line.split('" "', 1)[1].rstrip('"')
        # the shell the script's own check() would eval it in: $k the kiosk, $w the unit directory, $r the image root
        unit, session, kiosk, fallback = ((DUO / n).read_text() for n in ("opos.service", "opos-session", "opos-kiosk", "opos-fallback.service"))

        def verdict(**changed):
            with tempfile.TemporaryDirectory() as d:
                r = Path(d)
                w = r / "etc" / "systemd" / "system"
                w.mkdir(parents=True)
                (r / "usr" / "libexec" / "opos").mkdir(parents=True)
                files = {w / "opos.service": unit, w / "opos-fallback.service": fallback,
                         r / "usr/libexec/opos/opos-session": session, r / "usr/libexec/opos/opos-kiosk": kiosk}
                names = {"unit": w / "opos.service", "session": r / "usr/libexec/opos/opos-session", "kiosk": r / "usr/libexec/opos/opos-kiosk",
                         "fallback": w / "opos-fallback.service"}
                for key, text in changed.items():
                    files[names[key]] = text
                for path, text in files.items():
                    path.write_text(text)
                script = "r=%s\nw=%s\nk=$r/usr/libexec/opos/opos-kiosk\n%s\n" % (r, w, cond)
                return subprocess.run(["sh", "-c", script], capture_output=True, text=True).returncode

        self.assertEqual(verdict(), 0)
        for what, change in {
            "no RestartPreventExitStatus=75": {"unit": unit.replace("RestartPreventExitStatus=75\n", "")},
            "75 made a success": {"unit": unit.replace("RestartPreventExitStatus=75\n", "RestartPreventExitStatus=75\nSuccessExitStatus=75\n")},
            "the long start-limit window": {"unit": unit.replace("StartLimitIntervalSec=60", "StartLimitIntervalSec=30min")},
            "no OnFailure=": {"unit": unit.replace("OnFailure=opos-fallback.service\n", "")},
            "a session that execs phoc away": {"session": session.replace("-E /usr/libexec/opos/opos-kiosk <&3 3<&- &", "-E /usr/libexec/opos/opos-kiosk").replace("systemd-cat -t phoc", "exec systemd-cat -t phoc")},
            "a session that never exits 75": {"session": session.replace("exit 75", "exit 1")},
            "a kiosk without the marker": {"kiosk": kiosk.replace("opos-gave-up", "opos-gone")},
            "the old threshold": {"kiosk": kiosk.replace("giveup=6", "giveup=10")},
            "a fallback that does not start phosh": {"fallback": fallback.replace("systemctl start phosh.service", "true")},
        }.items():
            with self.subTest(what):
                self.assertNotEqual(verdict(**change), 0, "the check must fail for: " + what)

    def test_power_note_exists_and_the_readme_points_to_it(self):
        power = (DUO / "POWER.md").read_text()
        self.assertTrue(power.startswith("<!-- SPDX-License-Identifier: GPL-3.0-or-later -->"))
        self.assertIn("nothing in this note is implemented", power)
        self.assertIn("[POWER.md](POWER.md)", (DUO / "README.md").read_text())

    def test_the_unit_keeps_the_settings_the_fallback_depends_on(self):
        u = (DUO / "opos.service").read_text()
        for s in ("RestartMode=direct", "OnFailure=opos-fallback.service", "StartLimitBurst=3", "ExecStartPre=+chvt 7", "RestartPreventExitStatus=75"):
            self.assertIn(s, u)

    def test_the_unit_restarts_a_session_that_ended_except_the_give_up_and_keeps_the_original_start_limit(self):
        u = (DUO / "opos.service").read_text()
        unit_part, service_part = u.split("[Service]")
        # any end of the session restarts it, except status 75, which is opos-session's "the kiosk gave up": it fails the unit
        self.assertRegex(service_part, r"(?m)^Restart=always$")
        self.assertRegex(service_part, r"(?m)^RestartPreventExitStatus=75$")
        self.assertNotRegex(u, r"(?m)^SuccessExitStatus=", "75 as a success would end the unit without a failure, and OnFailure= would not run")
        self.assertNotRegex(u, r"(?m)^(KillMode|SendSIGKILL|Type)=", "the model and the give-up chain assume systemd's defaults here")
        # the original short window, in [Unit], not switched off: a long one counted every restart and try-restart against the fallback
        m = re.search(r"(?m)^StartLimitIntervalSec=(\S+)$", unit_part)
        self.assertIsNotNone(m, "StartLimitIntervalSec belongs in [Unit]")
        self.assertEqual(unit_seconds(m.group(1)), 60)
        self.assertRegex(unit_part, r"(?m)^StartLimitBurst=3$")
        self.assertNotRegex(u, r"(?m)^StartLimitAction=")
        # the fallback is what OnFailure= starts; it starts phosh unless opos.service is running (a failed state is not)
        fb = (DUO / "opos-fallback.service").read_text()
        self.assertIn("systemctl start phosh.service", fb)
        self.assertIn("active|activating|reloading) exit 0", fb)
        self.assertNotIn("failed", fb.split("case $s in")[1].split(")")[0], "a failed unit must fall through to starting phosh")

    def test_the_session_runs_phoc_as_a_child_and_exits_75_for_the_give_up_alone(self):
        text = SESSION.read_text()
        code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        phoc_lines = [ln for ln in code if "/usr/bin/phoc" in ln]
        self.assertEqual(len(phoc_lines), 1)
        self.assertTrue(phoc_lines[0].rstrip().endswith("&"), "phoc is a background child, not exec'd")
        self.assertFalse([ln for ln in code if re.match(r"exec\s+(systemd-cat|dbus-run-session|/usr/bin/phoc)", ln)])
        self.assertEqual([ln.strip() for ln in code if "exit 75" in ln], ["exit 75"])
        self.assertIn("opos-gave-up", text)
        self.assertIn("opos-gave-up", KIOSK.read_text(), "the kiosk writes the file the session looks for")
        self.assertRegex(text, r"(?m)^grace=\d+$")


def systemd_can_be_driven():
    return (os.environ.get("OPOS_TEST_SYSTEMD") == "1" and hasattr(os, "geteuid") and os.geteuid() == 0
            and os.path.isdir("/run/systemd/system") and shutil.which("systemctl") is not None)


@unittest.skipUnless(systemd_can_be_driven(), "set OPOS_TEST_SYSTEMD=1 and run as root on a machine whose PID 1 is systemd (it adds four transient units)")
class SystemdModelTest(unittest.TestCase):
    """The whole give-up chain on the real systemd, scaled in time. What is real: the shipped opos-session (only its two absolute
    paths, phoc and the kiosk, point at stand-ins), the shipped opos-kiosk, `pgrep`/`pkill`, and the shipped opos-fallback.service
    ExecStart (only the names opos.service and phosh.service are changed to the model's). The unit is built from the shipped
    opos.service's own lines: the start limit, Restart=, RestartSec=, RestartMode=, RestartPreventExitStatus=, TimeoutStartSec=.
    What is stand-in: phoc (a script that behaves as the test says), Chromium (dies at once), systemd-cat and dbus-run-session
    (wrappers that pass no signal on), item-composer-fresh (a 1 s ExecStartPre), phosh (a unit that leaves a marker), and every
    `sleep` the scripts run (10 times faster). Not the phone: no real phoc (whether it ends on TERM, how it shuts the hwcomposer
    down), no real durations, no PAM session or tty. It needs root and PID 1 systemd, and skips itself when a phoc is running."""

    BIN = {
        "chromium": 'if [ -e "$T/chromium_stays" ]; then exec /bin/sleep 3600; fi\necho run >> "$T/runs"\nexit 1',
        "sleep": 'exec /bin/sleep "$(awk "BEGIN { print $1 / 10 }")"',
        "python3": "exit 0",
        "logger": 'echo "$*" >> "$T/logger.log"',
        "systemd-cat": 'shift 2\nexec "$@"',
        "dbus-run-session": 'shift\nexec 4<&0\n"$@" <&4 4<&- &\nwait $!\nexit $?',
    }

    def setUp(self):
        if subprocess.run(["pgrep", "-x", "phoc"], capture_output=True).returncode == 0:
            self.skipTest("a phoc is running on this machine: opos-session ends phoc by name, and the model would signal it")
        self.t = Path(tempfile.mkdtemp(prefix="opos-model-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.t, True)
        (self.t / "bin").mkdir()
        (self.t / "home").mkdir()
        (self.t / "run").mkdir()
        for name, body in self.BIN.items():
            p = self.t / "bin" / name
            p.write_text("#!/bin/sh\n" + body + "\n")
            p.chmod(0o755)
        (self.t / "phoc").write_text(PHOC_STUB)
        (self.t / "phoc").chmod(0o755)
        shutil.copy(KIOSK, self.t / "kiosk")
        (self.t / "kiosk").chmod(0o755)
        session_copy(self.t / "opos-session", self.t / "phoc", self.t / "kiosk")
        self.name = "opos-model-test-%d" % os.getpid()
        self.fallback, self.phosh = self.name + "-fallback", self.name + "-phosh"
        self.addCleanup(self.cleanup_units)

    def systemctl(self, *args):
        return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=60)

    def cleanup_units(self):
        for n in (self.name, self.fallback, self.phosh):
            self.systemctl("kill", "--signal=SIGKILL", "--kill-whom=all", n + ".service")  # a phoc that ignores TERM would hold stop for 90 s
            self.systemctl("stop", n + ".service")
            self.systemctl("reset-failed", n + ".service")
            try:
                os.unlink("/run/systemd/system/%s.service" % n)
            except FileNotFoundError:
                pass
        self.systemctl("daemon-reload")

    def shipped(self, key, part="[Service]"):
        text = (DUO / "opos.service").read_text()
        text = text.split("[Service]")[1] if part == "[Service]" else text.split("[Service]")[0]
        return re.search(r"(?m)^%s=(\S+)$" % key, text).group(1)

    def fallback_exec(self):
        """The shipped ExecStart of opos-fallback.service, the two unit names changed to the model's."""
        line = next(ln for ln in (DUO / "opos-fallback.service").read_text().splitlines() if ln.startswith("ExecStart="))
        self.assertIn("opos.service", line)
        self.assertIn("systemctl start phosh.service", line)
        line = line.replace("opos.service", self.name + ".service").replace("phosh.service", self.phosh + ".service")
        self.assertFalse(os.path.exists("/usr/libexec/item/item-composer-fresh"), "the shipped fallback would run the port's composer script")
        return line

    def install(self, mode, prevent=True):
        t = self.t
        (t / "phoc_mode").write_text(mode + "\n")
        (t / "stdin").write_text("")
        d = Path("/run/systemd/system")
        (d / (self.phosh + ".service")).write_text(
            "[Unit]\nDescription=model of phosh.service\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/sh -c 'echo started > %s/phosh-started'\n" % t)
        (d / (self.fallback + ".service")).write_text(
            "[Unit]\nDescription=model of opos-fallback.service\n[Service]\nType=oneshot\n"
            "ExecStartPre=/bin/sh -c 'systemctl show %s.service -p ActiveState,Result,ExecMainStatus,NRestarts > %s/at-failure'\n%s\n" % (self.name, t, self.fallback_exec()))
        lines = ["[Unit]", "Description=scaled model of opos.service", "OnFailure=%s.service" % self.fallback,
                 "StartLimitIntervalSec=%s" % self.shipped("StartLimitIntervalSec", "[Unit]"), "StartLimitBurst=%s" % self.shipped("StartLimitBurst", "[Unit]"),
                 "[Service]", "Environment=T=%s" % t, "Environment=HOME=%s/home" % t, "Environment=XDG_RUNTIME_DIR=%s/run" % t,
                 "Environment=PATH=%s/bin:/usr/bin:/bin" % t, "StandardInput=file:%s/stdin" % t,
                 "ExecStartPre=/bin/sleep 1", "ExecStart=/bin/sh %s/opos-session" % t, "TimeoutStartSec=%s" % self.shipped("TimeoutStartSec"),
                 "Restart=%s" % self.shipped("Restart"), "RestartSec=%s" % self.shipped("RestartSec"), "RestartMode=%s" % self.shipped("RestartMode")]
        if prevent:
            lines.append("RestartPreventExitStatus=%s" % self.shipped("RestartPreventExitStatus"))
        (d / (self.name + ".service")).write_text("\n".join(lines) + "\n")
        self.assertEqual(self.systemctl("daemon-reload").returncode, 0)

    def diag(self):
        j = subprocess.run(["journalctl", "-u", self.name + ".service", "-u", self.fallback + ".service", "--no-pager", "-n", "60", "-o", "cat"],
                           capture_output=True, text=True).stdout
        return "\n" + self.systemctl("status", self.name + ".service", "--no-pager").stdout + j

    def run_until_phosh(self, mode, prevent=True, wait_s=45):
        """Starts the model; the seconds until the model phosh was started, or None."""
        self.install(mode, prevent)
        start = time.monotonic()
        self.systemctl("start", "--no-block", self.name + ".service")
        marker = self.t / "phosh-started"
        while time.monotonic() - start < wait_s:
            if marker.exists():
                return time.monotonic() - start
            time.sleep(0.1)
        return None

    def at_failure(self):
        return dict(ln.split("=", 1) for ln in (self.t / "at-failure").read_text().splitlines())

    def runs(self):
        try:
            return len((self.t / "runs").read_text().splitlines())
        except FileNotFoundError:
            return 0

    def assert_gave_up(self, took):
        self.assertIsNotNone(took, "OnFailure= never started phosh (the unit stays up with black panels):" + self.diag())
        at = self.at_failure()
        # the state when OnFailure= fired, before the fallback's reset-failed: failed, by the exit status 75, with no restart
        self.assertEqual((at["ActiveState"], at["Result"], at["ExecMainStatus"], at["NRestarts"]), ("failed", "exit-code", "75", "0"), at)
        self.assertEqual(self.runs(), 6, "one session, six quick Chromium exits, no second session" + self.diag())
        self.assertLess(took, 30, "the whole chain, scaled; a phoc left to systemd's stop timeout (90 s) would not make it")
        self.assertFalse(alive(int((self.t / "phoc.pid").read_text())), "the stub phoc outlived the unit")
        self.assertEqual(self.systemctl("is-active", self.name + ".service").stdout.strip(), "inactive", "the fallback's reset-failed leaves it inactive")

    def test_a_phoc_that_ignores_term_still_ends_in_the_fallback(self):
        # the report: one TERM to phoc and then nothing; phoc ignores it, the session never ends, the panels stay black for good
        self.assert_gave_up(self.run_until_phosh("ignore-term"))
        self.assertEqual((self.t / "phoc.sig").read_text().split(), ["TERM"], "asked once first, then killed")

    def test_a_phoc_that_ends_on_term_ends_in_the_fallback(self):
        self.assert_gave_up(self.run_until_phosh("obey-term"))

    def test_a_phoc_that_ends_with_its_client_ends_in_the_fallback(self):
        self.assert_gave_up(self.run_until_phosh("exit-with-client"))

    def test_without_RestartPreventExitStatus_the_give_up_would_be_a_restart(self):
        # the control: the same chain without the one line. The session is started again (three sessions of 6 Chromium exits
        # run; the 4th start is refused, and NRestarts counts that refused one), and the fallback is reached only through
        # the start limit: what the owner waited for before.
        took = self.run_until_phosh("exit-with-client", prevent=False)
        self.assertIsNotNone(took, self.diag())
        at = self.at_failure()
        # Result stays the first failure (exit-code); the restarts and the exit status tell the two ways to "failed" apart
        self.assertEqual((at["ActiveState"], at["NRestarts"], at["ExecMainStatus"]), ("failed", "3", "75"), at)
        self.assertEqual(self.runs(), 18)

    def test_a_phoc_that_exits_by_itself_is_restarted_and_a_fast_loop_reaches_the_fallback_through_the_short_start_limit(self):
        took = self.run_until_phosh("exit=3")
        self.assertIsNotNone(took, self.diag())
        at = self.at_failure()
        self.assertEqual((at["ActiveState"], at["NRestarts"], at["ExecMainStatus"]), ("failed", "3", "3"), at)
        self.assertEqual(self.runs(), 0, "the kiosk never ran: phoc died at once, each time")
        self.assertLess(took, 30)

    def test_a_phoc_that_exits_75_by_itself_is_an_ordinary_crash_not_a_give_up(self):
        took = self.run_until_phosh("exit=75")
        self.assertIsNotNone(took, self.diag())
        at = self.at_failure()
        self.assertEqual((at["NRestarts"], at["ExecMainStatus"]), ("3", "1"), "restarted like any crash, as status 1; 75 reaches the unit only from a marker")

    def test_stopping_the_unit_is_not_a_failure_and_starts_no_fallback(self):
        # opos-switch phosh: systemctl disable --now opos.service. The session shell is killed by SIGTERM, with its phoc.
        (self.t / "chromium_stays").write_text("")
        self.install("obey-term")
        self.systemctl("start", self.name + ".service")
        for _ in range(50):
            if (self.t / "phoc.pid").exists():
                break
            time.sleep(0.1)
        time.sleep(1)
        self.assertEqual(self.systemctl("is-active", self.name + ".service").stdout.strip(), "active", self.diag())
        self.assertEqual(self.systemctl("stop", self.name + ".service").returncode, 0)
        shown = dict(ln.split("=", 1) for ln in self.systemctl("show", self.name + ".service", "-p", "ActiveState,Result").stdout.splitlines())
        self.assertEqual(shown, {"ActiveState": "inactive", "Result": "success"})
        time.sleep(4)  # the fallback waits 2 s before it looks
        self.assertFalse((self.t / "phosh-started").exists(), "a stop must not start phosh through OnFailure=")

    def test_the_fallback_leaves_a_running_opos_alone(self):
        (self.t / "chromium_stays").write_text("")
        self.install("obey-term")
        self.systemctl("start", self.name + ".service")
        time.sleep(1)
        r = self.systemctl("start", self.fallback + ".service")  # as if OnFailure= had fired for a unit that is up
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.t / "phosh-started").exists(), "the fallback started phosh although opos.service is active")
        self.assertEqual(self.systemctl("is-active", self.name + ".service").stdout.strip(), "active")


if __name__ == "__main__":
    unittest.main()
