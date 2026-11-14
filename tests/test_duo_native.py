# SPDX-License-Identifier: GPL-3.0-or-later
"""The Duo OS native launcher (packaging/duo-os/opos-native) run under sh with a fake python3, logger, date and sleep
on PATH and a fake browser kiosk. The fake python3 answers the server wait and the GTK check, and for the shell itself
reads one line of a plan per start ("status seconds"); when the plan is spent it ends the script with SIGTERM.
Also the line in opos-session that chooses the launcher, and the exit statuses the shell promises the launcher."""
import ast
import contextlib
import io
import shutil
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUO = ROOT / "packaging" / "duo-os"
NATIVE = DUO / "opos-native"
SESSION = DUO / "opos-session"
KIOSK = DUO / "opos-kiosk"
SHELL = ROOT / "src" / "opos" / "native" / "shell.py"

FAKES = {
    "python3": r"""#!/bin/sh
case "$1" in
-c)
    case "$2" in
    *urlopen*)
        f=0
        [ -r "$T/py_fails" ] && read -r f < "$T/py_fails"
        [ "$f" -gt 0 ] || exit 0
        echo $((f - 1)) > "$T/py_fails"
        exit 1 ;;
    *) [ ! -e "$T/no_gtk" ]; exit $? ;;
    esac ;;
-m)
    if [ "$2" = opos.native.blank ]; then
        printf '%s %s %s\n' "$3" "${PYTHONPATH-unset}" "$(wc -l 2>/dev/null < "$T/kiosk.log" || echo 0)" >> "$T/restore.log"
        rc=0
        [ -r "$T/restore_rc" ] && read -r rc < "$T/restore_rc"
        exit "$rc"
    fi ;;
esac
n=0
[ -r "$T/count" ] && read -r n < "$T/count"
n=$((n + 1))
line=$(sed -n "${n}p" "$T/plan")
[ -n "$line" ] || { kill -TERM "$PPID"; exit 0; }
echo "$n" > "$T/count"
[ -e "$T/server_dies" ] && echo 1 > "$T/py_fails"
case "$line" in
hang*) exec /bin/sleep 30 ;;
ready*) : > "$OPOS_NATIVE_READY"; /bin/sleep 1; exit 1 ;;
esac
{
    printf 'RUN\t%s\n' "$*"
    printf 'SPAN\t%s\n' "${OPOS_SPAN-unset}"
    printf 'PATH\t%s\n' "${PYTHONPATH-unset}"
    printf 'GDK\t%s\n' "${GDK_BACKEND-unset}"
    printf 'TZ\t%s\n' "${TZ-unset}"
    printf 'WAYLAND_SOCKET\t%s\n' "${WAYLAND_SOCKET-unset}"
    printf 'APPS\t%s\n' "${OPOS_APPS-unset}"
} >> "$T/shell.log"
read -r clock < "$T/clock"
set -- $line
echo $((clock + $2)) > "$T/clock"
exit "$1"
""",
    "date": '#!/bin/sh\nread -r c < "$T/clock"\necho "$c"\n',
    "sleep": '#!/bin/sh\necho "$1" >> "$T/sleeps"\nread -r c < "$T/clock"\necho $((c + $1)) > "$T/clock"\n',
    "logger": '#!/bin/sh\necho "$*" >> "$T/logger.log"\n',
    "kiosk": '#!/bin/sh\necho "kiosk pid $$ span ${OPOS_SPAN-unset} tried ${OPOS_NATIVE_TRIED-unset}" >> "$T/kiosk.log"\n'
             'echo "${OPOS_NATIVE_RESTORE_FAILED-unset}" >> "$T/kiosk.failed"\nexit 0\n',
}


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class NativeLauncherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.bin = self.t / "bin"
        self.bin.mkdir()
        self.home = self.t / "home"
        self.home.mkdir()
        for name, body in FAKES.items():
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        (self.t / "clock").write_text("1000\n")
        (self.t / "py_fails").write_text("0\n")
        self.config = self.home / ".config" / "opos"
        self.config.mkdir(parents=True)
        (self.config / "firstrun.json").write_text("{}\n")      # first-run setup is done; the tests of that remove it

    def run_native(self, plan, env=None, defaults=None, returncode=0):
        (self.t / "plan").write_text("".join("%s %s\n" % p for p in plan))
        e = {"PATH": "%s:/usr/bin:/bin" % self.bin, "T": str(self.t), "HOME": str(self.home),
             "OPOS_NATIVE_DEFAULTS": str(defaults or self.t / "no-such-defaults"), "OPOS_NATIVE_BROWSER": str(self.bin / "kiosk"),
             "XDG_RUNTIME_DIR": str(self.t), "OPOS_NATIVE_START_S": "0",  # 0: the start watcher has its own tests below
             "OPOS_NATIVE_APPS_CONF": str(self.t / "no-such-apps-conf")}
        e.update(env or {})
        r = subprocess.run(["sh", str(NATIVE)], env=e, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, returncode, r.stderr)
        return r

    def read(self, name):
        try:
            return (self.t / name).read_text().splitlines()
        except FileNotFoundError:
            return []

    def runs(self):
        out = []
        for line in self.read("shell.log"):
            k, v = line.split("\t", 1)
            if k == "RUN":
                out.append({"run": v})
            else:
                out[-1][k] = v
        return out

    def kiosk_started(self):
        return len(self.read("kiosk.log"))

    # --- what the shell is started with ------------------------------------------------------------------------
    def test_the_shell_is_started_from_the_installed_release_on_wayland(self):
        self.run_native([(1, 100)], returncode=-signal.SIGTERM)
        (run,) = self.runs()
        self.assertEqual(run["run"], "-m opos.native.shell")
        self.assertEqual(run["PATH"], "/opt/opos/src")
        self.assertEqual(run["GDK"], "wayland")
        self.assertEqual(self.kiosk_started(), 0, "the browser is not started while the shell runs")

    def test_a_spent_wayland_socket_is_not_handed_to_the_shell(self):
        self.run_native([(1, 100)], env={"WAYLAND_SOCKET": "7"}, returncode=-signal.SIGTERM)
        self.assertEqual(self.runs()[0]["WAYLAND_SOCKET"], "unset")

    def test_the_shell_gets_the_servers_everyday_apps_from_its_drop_in(self):
        conf = self.t / "20-opos-apps.conf"
        conf.write_text("# a comment\n[Service]\nEnvironment=OPOS_APPS=clock,contacts,notes,calc,calls\n")
        self.run_native([(1, 100)], env={"OPOS_NATIVE_APPS_CONF": str(conf)}, returncode=-signal.SIGTERM)
        self.assertEqual(self.runs()[0]["APPS"], "clock,contacts,notes,calc,calls")

    def test_no_drop_in_or_a_strange_line_means_no_everyday_apps(self):
        self.run_native([(1, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(self.runs()[0]["APPS"], "unset", "absent: as on the server, no apps")
        conf = self.t / "20-opos-apps.conf"
        conf.write_text("Environment=OPOS_APPS=clock;reboot\nEnvironment=\"OPOS_APPS=notes\"\n")
        (self.t / "count").unlink()
        self.run_native([(1, 100)], env={"OPOS_NATIVE_APPS_CONF": str(conf)}, returncode=-signal.SIGTERM)
        self.assertEqual(self.runs()[-1]["APPS"], "", "only a plain list is taken; anything else is no apps")

    def test_an_opos_apps_already_set_is_kept(self):
        conf = self.t / "20-opos-apps.conf"
        conf.write_text("Environment=OPOS_APPS=clock\n")
        self.run_native([(1, 100)], env={"OPOS_NATIVE_APPS_CONF": str(conf), "OPOS_APPS": "notes"}, returncode=-signal.SIGTERM)
        self.assertEqual(self.runs()[0]["APPS"], "notes")

    def test_every_handover_to_the_browser_lights_the_screens_first(self):
        for plan, why, n in (([(10, 500)], "the owner chose", 1), ([(11, 5)], "the PIN lock", 1),
                             ([(1, 2)] * 3, "three quick ends", 3)):
            with self.subTest(why=why):
                for f in ("count", "restore.log", "kiosk.log", "shell.log"):
                    (self.t / f).unlink(missing_ok=True)
                self.run_native(plan)
                self.assertEqual(self.read("restore.log"), ["--restore /opt/opos/src 0"] * n,
                                 "from the installed release, before the browser started (and after each unclean end"
                                 " that was not handed over: the last is restored once, by the handover)")
                self.assertEqual(self.kiosk_started(), 1)

    def test_a_restore_that_could_not_light_the_screens_is_logged_and_the_browser_still_starts(self):
        (self.t / "restore_rc").write_text("1\n")
        self.run_native([(10, 500)])
        self.assertEqual(len(self.read("restore.log")), 1)
        self.assertEqual(self.kiosk_started(), 1, "the handover is never stopped")
        self.assertTrue(any("could not light both screens before the browser takes over" in m for m in self.read("logger.log")),
                        self.read("logger.log"))
        self.assertEqual(self.read("kiosk.failed"), ["1"], "the kiosk is told, so it keeps trying in the background")

    def test_a_restore_that_lit_the_screens_tells_the_kiosk_nothing_is_left(self):
        self.run_native([(10, 500)], env={"OPOS_NATIVE_RESTORE_FAILED": "1"})
        self.assertEqual(self.read("kiosk.failed"), ["unset"], "a stale flag from the caller is not passed on")

    def test_a_restore_after_clean_and_sigterm_ends_too(self):
        """Round 6: a shell that ended with 0 or 143 lit the screens itself only if the panels took the write; when they
        refused it, they stay dark. The launcher restores after every end (a no-op when nothing is dark)."""
        self.run_native([(0, 100), (143, 100), (0, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(self.read("restore.log"), ["--restore /opt/opos/src 0"] * 3, "after every end, 0 and 143 included")

    def test_a_shell_that_ended_any_other_way_lights_the_screens_before_the_pause(self):
        """R5: a shell killed outright (KILL, a crash) while the lid was closed leaves the screens dark; the launcher lights
        them before its 2 s pause and the wait for the server, not only at the next start."""
        (self.bin / "sleep").write_text(FAKES["sleep"].replace(
            'echo "$1" >> "$T/sleeps"', 'echo "$1 $(cat "$T/restore.log" 2>/dev/null | wc -l | tr -d " ")" >> "$T/sleeps"'))
        self.run_native([(137, 100), (0, 100), (1, 100), (143, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(self.read("restore.log"), ["--restore /opt/opos/src 0"] * 4, "after every end (round 6)")
        self.assertEqual(self.read("sleeps"), ["2 1", "2 2", "2 3", "2 4"], "each before the pause that follows it")
        (self.t / "restore_rc").write_text("1\n")
        for f in ("count", "restore.log", "sleeps", "shell.log"):
            (self.t / f).unlink(missing_ok=True)
        self.run_native([(139, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(len(self.read("restore.log")), 1)
        self.assertTrue(any("after the shell ended with status 139" in m for m in self.read("logger.log")), self.read("logger.log"))
        self.assertEqual(len(self.runs()), 1, "a failed restore never stops the next start")

    def test_the_span_comes_from_the_defaults_file(self):
        d = self.t / "defaults"
        d.write_text("OPOS_SPAN=675,42\n")
        self.run_native([(1, 100)], defaults=d, returncode=-signal.SIGTERM)
        self.assertEqual(self.runs()[0]["SPAN"], "675,42")

    def test_no_defaults_file_means_one_window_with_no_gap(self):
        self.run_native([(1, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(self.runs()[0]["SPAN"], "unset")

    def test_the_defaults_file_is_read_never_run(self):
        d, nl = self.t / "defaults", chr(10)
        for text, span in (("OPOS_SPAN=675,42" + nl + "exit 3" + nl, "675,42"), ("exit 0" + nl + "OPOS_SPAN=675,42" + nl, "675,42"),
                           ("OPOS_SPAN=1,2" + nl + "OPOS_SPAN=675,42" + nl, "675,42"), ("OPOS_SPAN=$(reboot)" + nl, "unset"),
                           ("OPOS_SPAN=675,42; touch " + str(self.t / "ran") + nl, "unset"), ("# OPOS_SPAN=675,42" + nl, "unset")):
            with self.subTest(text=text):
                (self.t / "count").unlink(missing_ok=True)
                (self.t / "shell.log").unlink(missing_ok=True)
                d.write_text(text)
                self.run_native([(1, 100)], defaults=d, env={"OPOS_SPAN": "9,9"}, returncode=-signal.SIGTERM)
                self.assertEqual(self.runs()[0]["SPAN"], span, "only the file decides, and only a line of the right shape")
                self.assertFalse((self.t / "ran").exists())

    def test_the_owners_time_zone_is_read_at_every_start_and_a_bad_one_is_ignored(self):
        cfg = self.config
        (cfg / "timezone").write_text("Asia/Manila\n")
        self.run_native([(1, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(self.runs()[0]["TZ"], "Asia/Manila")
        for bad in ("../etc/passwd", "/etc/localtime", "a b", "-x"):
            (self.t / "count").unlink(missing_ok=True)
            (self.t / "shell.log").unlink(missing_ok=True)
            (cfg / "timezone").write_text(bad + "\n")
            self.run_native([(1, 100)], returncode=-signal.SIGTERM)
            self.assertEqual(self.runs()[0]["TZ"], "unset", bad)

    def test_it_waits_for_the_server_before_every_start(self):
        (self.t / "py_fails").write_text("3\n")
        self.run_native([(1, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(self.read("sleeps")[:3], ["1", "1", "1"])
        self.assertEqual(len(self.runs()), 1)

    def test_the_wait_is_before_every_start_not_only_the_first(self):
        (self.t / "server_dies").write_text("")      # the server goes down each time the shell starts
        self.run_native([(1, 100), (1, 100), (1, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(self.read("sleeps"), ["2", "1", "2", "1", "2", "1"], "2 s after each end, then one wait for the server")

    def test_a_server_that_never_answers_does_not_stop_the_shell(self):
        (self.t / "py_fails").write_text("500\n")
        self.run_native([(1, 100)], returncode=-signal.SIGTERM)
        self.assertEqual(len(self.runs()), 1)
        self.assertTrue(any("does not answer" in line for line in self.read("logger.log")))

    # --- when the browser takes over ---------------------------------------------------------------------------
    def test_the_pin_lock_starts_the_browser_because_the_shell_has_no_pin_screen(self):
        self.run_native([(1, 100)], env={"OPOS_LOCK": "1"})
        self.assertEqual((len(self.runs()), self.kiosk_started()), (0, 1), "the shell is never started")
        self.assertTrue(any("PIN lock is on" in line for line in self.read("logger.log")))

    def test_no_lock_or_a_lock_that_is_off_leaves_the_shell_running(self):
        for env in ({}, {"OPOS_LOCK": "0"}, {"OPOS_LOCK": ""}):
            with self.subTest(env=env):
                (self.t / "count").unlink(missing_ok=True)
                (self.t / "shell.log").unlink(missing_ok=True)
                (self.t / "kiosk.log").unlink(missing_ok=True)
                self.run_native([(1, 100)], env=env, returncode=-signal.SIGTERM)
                self.assertEqual((len(self.runs()), self.kiosk_started()), (1, 0))

    def test_a_shell_that_found_the_server_locked_ends_with_11_and_the_browser_starts(self):
        self.run_native([(11, 500)])
        self.assertEqual((len(self.runs()), self.kiosk_started()), (1, 1))
        self.assertTrue(any("PIN lock" in line for line in self.read("logger.log")))

    def test_the_browser_keeps_the_screen_until_first_run_setup_is_finished(self):
        (self.config / "firstrun.json").unlink()
        self.run_native([(1, 100)])
        self.assertEqual((len(self.runs()), self.kiosk_started()), (0, 1), "setup is a browser page")
        self.assertTrue(any("first-run setup is not finished" in line for line in self.read("logger.log")))

    def test_the_setup_flag_is_looked_for_where_the_server_writes_it(self):
        (self.config / "firstrun.json").unlink()
        other = self.t / "elsewhere"
        other.mkdir()
        (other / "firstrun.json").write_text("{}\n")
        self.run_native([(1, 100)], env={"OPOS_CONFIG_DIR": str(other)}, returncode=-signal.SIGTERM)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (1, 0), "OPOS_CONFIG_DIR wins, as it does for the server")

    def test_the_launcher_never_tells_anyone_to_restart_the_phone_or_the_compositor(self):
        text = NATIVE.read_text(encoding="utf-8")
        for words in ("systemctl restart", "restart opos", "restart phoc", "compositor", "reboot"):
            self.assertNotIn(words, text)

    def test_no_gtk_goes_straight_to_the_browser(self):
        (self.t / "no_gtk").write_text("")
        self.run_native([(0, 100)])
        self.assertEqual((len(self.runs()), self.kiosk_started()), (0, 1))

    def test_the_owners_choice_starts_the_browser_at_once(self):
        self.run_native([(10, 500)])
        self.assertEqual((len(self.runs()), self.kiosk_started()), (1, 1))
        self.assertTrue(any("owner chose" in line for line in self.read("logger.log")))

    def test_the_browser_takes_over_the_same_process(self):
        (self.t / "plan").write_text("10 5" + chr(10))
        r = subprocess.Popen(["sh", str(NATIVE)], env={
            "PATH": "%s:/usr/bin:/bin" % self.bin, "T": str(self.t), "HOME": str(self.home), "XDG_RUNTIME_DIR": str(self.t),
            "OPOS_NATIVE_START_S": "0", "OPOS_NATIVE_DEFAULTS": str(self.t / "none"), "OPOS_NATIVE_BROWSER": str(self.bin / "kiosk")})
        self.assertEqual(r.wait(timeout=60), 0)
        self.assertEqual(self.read("kiosk.log"), ["kiosk pid %d span unset tried 1" % r.pid], "exec, not a child: phoc's client stays one process")

    def test_three_quick_ends_in_a_row_start_the_browser(self):
        self.run_native([(1, 2), (1, 2), (1, 2), (1, 2)])
        self.assertEqual((len(self.runs()), self.kiosk_started()), (3, 1))

    def test_two_quick_ends_then_a_long_run_start_over(self):
        self.run_native([(1, 2), (1, 2), (0, 300), (1, 2), (1, 2)], returncode=-signal.SIGTERM)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (5, 0))
        self.assertEqual([s for s in self.read("sleeps")], ["2"] * 5)

    def test_a_clean_end_is_started_again_not_handed_to_the_browser(self):
        self.run_native([(0, 400), (0, 400)], returncode=-signal.SIGTERM)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (2, 0))

    def test_six_ends_within_ten_minutes_start_the_browser_however_long_each_ran(self):
        self.run_native([(1, 60)] * 8)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (6, 1))

    def test_five_slow_ends_do_not(self):
        self.run_native([(1, 60)] * 5, returncode=-signal.SIGTERM)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (5, 0))

    def test_ends_spread_over_more_than_ten_minutes_do_not(self):
        self.run_native([(1, 200)] * 9, returncode=-signal.SIGTERM)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (9, 0))

    def test_six_ends_across_a_clock_that_went_back_do_not(self):
        self.run_native([(1, 30)] * 5 + [(1, -5000)], returncode=-signal.SIGTERM)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (6, 0))

    def test_being_asked_to_start_again_is_not_a_quick_end(self):
        self.run_native([(143, 2)] * 5, returncode=-signal.SIGTERM)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (5, 0), "an update or a new time zone ends the shell with SIGTERM")

    def test_but_six_such_ends_within_ten_minutes_still_start_the_browser(self):
        self.run_native([(143, 2)] * 8)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (6, 1))

    # --- a shell that never puts its window up -----------------------------------------------------------------
    def test_a_shell_that_does_not_say_it_is_ready_is_ended_and_the_third_time_the_browser_starts(self):
        self.run_native([("hang", 0)] * 4, env={"OPOS_NATIVE_START_S": "2"})
        self.assertEqual(self.kiosk_started(), 1)
        late = [line for line in self.read("logger.log") if "had not put its window up after 2s" in line]
        self.assertEqual(len(late), 3)
        self.assertFalse((self.t / "opos-native-ready").exists())

    def test_a_shell_that_says_it_is_ready_is_left_alone(self):
        (self.bin / "sleep").write_text("#!/bin/sh" + chr(10) + "exec /bin/sleep 0.2" + chr(10))   # real pauses for this one
        self.run_native([("ready", 0)], env={"OPOS_NATIVE_START_S": "3"}, returncode=-signal.SIGTERM)
        self.assertFalse([line for line in self.read("logger.log") if "had not put its window up" in line])
        self.assertTrue(any("ended with status 1" in line for line in self.read("logger.log")), "it ended by itself, not by the watcher")

    def test_a_missing_browser_ends_the_client_with_status_1(self):
        self.run_native([(10, 5)], env={"OPOS_NATIVE_BROWSER": str(self.t / "no-such-kiosk")}, returncode=1)
        self.assertTrue(any("cannot start" in line for line in self.read("logger.log")))

    def test_a_clock_that_went_back_is_not_a_quick_end(self):
        (self.bin / "python3").write_text(FAKES["python3"].replace("echo $((clock + $2))", "echo $((clock - 50))"))
        self.run_native([(1, 0)] * 5, returncode=-signal.SIGTERM)
        self.assertEqual((len(self.runs()), self.kiosk_started()), (5, 0))


class TheKioskHandsOver(unittest.TestCase):
    """opos-kiosk stays phoc's client; its first act is to hand its process to the launcher, where there is one."""

    @unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
    def test_the_kiosk_hands_its_process_to_the_launcher_and_gets_it_back_once(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            (t / "bin").mkdir()
            for name, body in FAKES.items():
                (t / "bin" / name).write_text(body)
                (t / "bin" / name).chmod(0o755)
            # a launcher that says who it is and goes back to the real kiosk the way opos-native does
            def script(path, *lines):
                path.write_text("\n".join(("#!/bin/sh",) + lines) + "\n")
                path.chmod(0o755)
            script(t / "native", 'echo "native pid $$" >> "$T/order"', "OPOS_NATIVE_TRIED=1", "export OPOS_NATIVE_TRIED", 'exec sh "$KIOSK"')
            script(t / "bin" / "chromium", 'echo "chromium parent $PPID" >> "$T/order"', 'kill -TERM "$PPID"')
            script(t / "bin" / "pgrep", "exit 1")
            env = {"PATH": "%s:/usr/bin:/bin" % (t / "bin"), "T": str(t), "HOME": str(t), "XDG_RUNTIME_DIR": str(t), "KIOSK": str(KIOSK),
                   "OPOS_KIOSK_DEFAULTS": str(t / "none"), "OPOS_NATIVE": str(t / "native"), "OPOS_NATIVE_OFF": str(t / "native-off")}
            (t / "clock").write_text("1000" + chr(10))
            r = subprocess.Popen(["sh", str(KIOSK)], env=env)
            self.assertEqual(r.wait(timeout=60), -signal.SIGTERM)
            self.assertEqual((t / "order").read_text().splitlines(), ["native pid %d" % r.pid, "chromium parent %d" % r.pid],
                             "one process throughout: the launcher, then the browser kiosk, never the launcher twice")
            self.assertFalse((t / "restore.log").exists(), "handed back by the launcher: it has lit the screens already")
            # switched off: the launcher is never started, so the kiosk lights the screens itself before Chromium
            (t / "order").unlink()
            (t / "native-off").write_text("")
            r = subprocess.Popen(["sh", str(KIOSK)], env=env)
            self.assertEqual(r.wait(timeout=60), -signal.SIGTERM)
            self.assertEqual((t / "order").read_text().splitlines(), ["chromium parent %d" % r.pid])
            self.assertEqual((t / "restore.log").read_text().splitlines(), ["--restore /opt/opos/src 0"],
                             "a shell killed while the lid was closed cannot leave the browser on dark screens")

    def test_the_session_script_is_untouched_and_the_kiosk_names_the_launcher(self):
        self.assertEqual(SESSION.read_text(encoding="utf-8").count("-E /usr/libexec/opos/opos-kiosk <&3 3<&- &"), 1)
        kiosk = KIOSK.read_text(encoding="utf-8")
        self.assertIn("native=${OPOS_NATIVE-/usr/libexec/opos/opos-native}", kiosk)
        self.assertIn('[ ! -e "${OPOS_NATIVE_OFF:-/etc/opos/native-off}" ]', kiosk)
        self.assertLess(kiosk.index('exec "$native"'), kiosk.index("unset OPOS_KIOSK_DEBUG_PORT"), "before the kiosk does anything of its own")
        native = NATIVE.read_text(encoding="utf-8")
        self.assertLess(native.index("export OPOS_NATIVE_TRIED"), native.index('exec "$browser"'), "or the kiosk would hand the process straight back")

    def test_both_scripts_are_plain_posix_shell_with_unix_line_ends(self):
        for path in (NATIVE, SESSION, KIOSK):
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"#!/bin/sh\n"), path.name)
            self.assertNotIn(b"\r", raw, path.name)

    def test_the_shell_promises_the_statuses_the_launcher_reads(self):
        tree = ast.parse(SHELL.read_text(encoding="utf-8"))
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
                  if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)}
        self.assertEqual((consts["LEAVE"], consts["BROKEN"], consts["LOCKED"]), (10, 3, 11))
        self.assertIn('if [ "$rc" -eq 10 ]; then', NATIVE.read_text(encoding="utf-8"))
        self.assertIn('if [ "$rc" -eq 11 ]; then', NATIVE.read_text(encoding="utf-8"))
        self.assertIn("self.shell.leave)", (SHELL.parent / "screens.py").read_text(encoding="utf-8"), "the Settings button ends with LEAVE")
        self.assertIn("OPOS_NATIVE_READY=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/opos-native-ready", NATIVE.read_text(encoding="utf-8"))
        shell = SHELL.read_text(encoding="utf-8")
        self.assertIn("logic.Ready(self.say_ready)", shell, "the shell says so, once, through logic.Ready")
        for need in ('"mapped"', '"warmed"', '"painted"'):
            self.assertIn("self.ready.mark(%s)" % need, shell, "and only after the window is mapped, warm and has drawn a frame")


BUILD = DUO / "build-image.sh"
VERIFY = DUO / "verify-image.sh"


def between(path, start, end):
    text = path.read_text(encoding="utf-8")
    i = text.index(start)
    return text[i:text.index(end, i) + len(end)]


@unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
class ImageIntegration(unittest.TestCase):
    """build-image.sh installs the launcher, verify-image.sh checks it, and neither touches the dev span block."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.r = self.t / "root"
        (self.r / "usr/libexec/opos").mkdir(parents=True)

    def run_sh(self, text, **env):
        script = self.t / "block.sh"
        script.write_text("set -eu\n" + text)
        return subprocess.run(["sh", str(script)], env={"PATH": "/usr/bin:/bin", **env}, capture_output=True, text=True, timeout=60)

    def build_native(self):
        block = between(BUILD, "# --- native shell: BEGIN", "# --- native shell: END")
        p = self.run_sh("r=%s\nhere=%s\n%s\n" % (self.r, DUO, block))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_launcher_and_the_span_file_are_installed_with_their_modes(self):
        self.build_native()
        launcher, defaults = self.r / "usr/libexec/opos/opos-native", self.r / "etc/default/opos-native"
        self.assertEqual(launcher.read_bytes(), NATIVE.read_bytes())
        self.assertEqual((oct(launcher.stat().st_mode & 0o777), oct(defaults.stat().st_mode & 0o777)), ("0o755", "0o644"))
        self.assertEqual([ln for ln in defaults.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")], ["OPOS_SPAN=675,42"])

    def test_the_span_file_says_what_the_server_unit_says(self):
        unit = (DUO / "opos-server.service").read_text(encoding="utf-8")
        self.build_native()
        line = [ln for ln in (self.r / "etc/default/opos-native").read_text(encoding="utf-8").splitlines() if not ln.startswith("#")][0]
        self.assertIn("Environment=" + line + "\n", unit)

    def test_it_is_not_written_inside_the_dev_span_block_and_that_block_is_unchanged(self):
        text = BUILD.read_text(encoding="utf-8")
        dev = text[text.index("# --- dev span: BEGIN"):text.index("# --- dev span: END")]
        self.assertNotIn("opos-native", dev)
        self.assertLess(text.index("# --- native shell: END"), text.index("# --- dev span: BEGIN"))
        self.assertEqual(text.count("# --- native shell: BEGIN"), 1)

    def verify_native(self):
        block = between(VERIFY, "# --- track native:", 'check "pgrep and pkill')
        block = block[:block.index('check "pgrep and pkill')]
        script = self.t / "verify.sh"
        script.write_text('r=%s; w=$r/etc/systemd/system; k=$r/usr/libexec/opos/opos-kiosk; fail=0\n'
                          'check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n%s\nexit $fail\n' % (self.r, block))
        return subprocess.run(["sh", str(script)], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60)

    def image(self):
        self.build_native()
        (self.r / "usr/libexec/opos/opos-kiosk").write_bytes(KIOSK.read_bytes())
        (self.r / "etc/systemd/system").mkdir(parents=True)
        shutil.copy(DUO / "opos-server.service", self.r / "etc/systemd/system/opos-server.service")
        n = self.r / "opt/opos/src/opos/native"
        n.mkdir(parents=True)
        for name in ("shell.py", "screens.py", "logic.py", "layout.py", "ui.py", "posture.py", "blank.py", "displayconfig.py"):
            shutil.copy(SHELL.parent / name, n / name)

    def test_a_good_image_passes_every_native_check(self):
        self.image()
        p = self.verify_native()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.count("PASS "), 3)
        self.assertIn("GTK 4 or PyGObject is not in this image: the launcher falls back", p.stdout)

    def test_a_missing_or_non_executable_launcher_fails(self):
        self.image()
        launcher = self.r / "usr/libexec/opos/opos-native"
        launcher.chmod(0o644)
        self.assertIn("FAIL the native shell launcher", self.verify_native().stdout)
        launcher.unlink()
        self.assertIn("FAIL the native shell launcher", self.verify_native().stdout)

    def test_a_kiosk_that_does_not_hand_over_fails(self):
        self.image()
        k = self.r / "usr/libexec/opos/opos-kiosk"
        k.write_text(k.read_text(encoding="utf-8").replace(">>> track native", ">>> track nothing"), encoding="utf-8")
        self.assertIn("FAIL the native shell launcher", self.verify_native().stdout)

    def test_the_assistants_screen_in_an_image_fails(self):
        self.image()
        (self.r / "opt/opos/src/opos/native/assistants.py").write_text("x = 1\n")
        self.assertIn("FAIL the native shell is in the image", self.verify_native().stdout)

    def test_a_span_that_differs_from_the_servers_or_holds_more_fails(self):
        self.image()
        d = self.r / "etc/default/opos-native"
        for body in ("OPOS_SPAN=700,42\n", "OPOS_SPAN=675,42\nOPOS_ASK=1\n", "OPOS_SPAN=$(reboot)\n"):
            with self.subTest(body=body):
                d.write_text(body, encoding="utf-8")
                self.assertIn("FAIL the native shell's defaults file", self.verify_native().stdout)
        d.unlink()
        self.assertNotIn("FAIL the native shell's defaults", self.verify_native().stdout, "no file is fine: one window with no gap")

    def test_gtk_in_the_image_is_noted(self):
        self.image()
        (self.r / "usr/lib/aarch64-linux-gnu/girepository-1.0").mkdir(parents=True)
        (self.r / "usr/lib/aarch64-linux-gnu/girepository-1.0/Gtk-4.0.typelib").write_text("x")
        (self.r / "usr/lib/python3/dist-packages/gi").mkdir(parents=True)
        (self.r / "usr/lib/python3/dist-packages/gi/__init__.py").write_text("x")
        self.assertIn("NOTE: GTK 4 and PyGObject are in the image", self.verify_native().stdout)


class Anything(type):
    """A class whose missing attributes are more such classes: Gtk.Stack, Gtk.Orientation.VERTICAL, and so on."""

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        made = Anything(name, (Thing,), {})
        setattr(cls, name, made)
        return made


class Thing(metaclass=Anything):
    """Stands in for any toolkit object: it can be made, called, subclassed and asked for anything."""

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return Thing()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Thing()


class Toolkit(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        made = Anything(name, (Thing,), {})
        setattr(self, name, made)
        return made


class Window(Thing):
    destroyed = 0

    def destroy(self):
        Window.destroyed += 1


class App:
    """A Gtk.Application that runs `activate` once and returns what the test told it to."""
    status, last = 0, None

    def __init__(self, *a, **k):
        App.last, self.flags, self.windows, self.quits = self, k.get("flags"), [Window()], 0

    def connect(self, _name, fn):
        self.activate = fn

    def run(self, _argv):
        self.activate(self)
        return App.status

    def get_windows(self):
        return self.windows

    def quit(self):
        self.quits += 1


class ShellMain(unittest.TestCase):
    """main() with a stand-in toolkit: what the process ends with, which is all the launcher can see."""

    def setUp(self):
        names = ("gi", "gi.repository", "opos.native.shell", "opos.native.screens", "opos.native.ui")
        saved = {n: sys.modules.get(n) for n in names}

        def restore():
            for n, m in saved.items():
                sys.modules.pop(n, None)
                if m is not None:
                    sys.modules[n] = m
        self.addCleanup(restore)
        for n in names:
            sys.modules.pop(n, None)
        gi = Toolkit("gi")
        gi.require_version = lambda *a: None
        gi.repository = Toolkit("gi.repository")
        sys.modules["gi"], sys.modules["gi.repository"] = gi, gi.repository
        import importlib
        self.shell = importlib.import_module("opos.native.shell")
        self.shell.Gtk.Application = App
        self.shell.Gio.ApplicationFlags.NON_UNIQUE = "non-unique"
        App.status, Window.destroyed = 0, 0

    def made(self, leaving=0, error=None):
        def build(*_a):
            if error:
                raise error
            w = Thing()
            w.leaving = leaving
            return w
        self.shell.Shell = build

    def test_a_window_that_cannot_be_built_ends_with_broken_and_leaves_nothing_behind(self):
        self.made(error=RuntimeError("a page could not be built"))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.shell.main(["shell"]), self.shell.BROKEN)
        self.assertEqual((App.last.quits, Window.destroyed), (1, 1), "the half-built window is destroyed and the application ends")
        self.assertIn("a page could not be built", err.getvalue(), "and the reason is in the log")

    def test_the_owners_choice_ends_with_leave(self):
        self.made(leaving=10)
        self.assertEqual(self.shell.main(["shell"]), self.shell.LEAVE)

    def test_a_pin_lock_ends_with_locked(self):
        self.made(leaving=11)
        self.assertEqual(self.shell.main(["shell"]), self.shell.LOCKED)

    def test_any_other_end_is_the_toolkits_own_status(self):
        self.made()
        for status in (0, 1):
            App.status = status
            self.assertEqual(self.shell.main(["shell"]), status)

    def test_a_second_start_is_its_own_process_not_a_wake_up_of_the_first(self):
        self.made()
        self.shell.main(["shell"])
        self.assertEqual(App.last.flags, "non-unique")

    def test_ready_is_written_where_the_launcher_looks_and_a_bad_path_is_no_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "opos-native-ready"
            self.shell.Shell.say_ready({"OPOS_NATIVE_READY": str(path)})
            self.assertTrue(path.is_file())
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertFalse(self.shell.Shell.say_ready({"OPOS_NATIVE_READY": str(Path(d) / "no" / "such" / "folder")}))
            self.assertIn("cannot write the ready file", err.getvalue(), "a failed write is in the log, not swallowed")
            self.assertFalse(self.shell.Shell.say_ready({}))


if __name__ == "__main__":
    unittest.main()
