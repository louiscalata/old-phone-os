# SPDX-License-Identifier: GPL-3.0-or-later
"""The OPOS OS clock: opos-clock against a fake root and a fake date, and the clock blocks of the image scripts."""
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "packaging" / "duo-os"
SH = shutil.which("sh")
MIN = 1767225600  # 2026-01-01T00:00:00Z
SPAN = 631152000
FLOOR = 1794093790  # 2026-11-07T23:23:10Z, a commit time
GIT = shutil.which("git")

FAKE_DATE = """#!/bin/sh
case "$*" in
  "-u +%s") cat "$FAKE/now" ;;
  "-u -s @"*) [ -z "${FAKE_FAIL_SET:-}" ] || exit 1; echo "${3#@}" > "$FAKE/now"; echo "${3#@}" >> "$FAKE/sets" ;;
  *) echo "fake date: $*" >&2; exit 9 ;;
esac
"""


def block(path, name):
    text = (PKG / path).read_text(encoding="utf-8")
    m = re.search(r"^# --- %s: BEGIN.*?\n(.*?)^# --- %s: END\n" % (name, name), text, re.S | re.M)
    assert m, (path, name)
    return m.group(1)


@unittest.skipUnless(SH and os.name == "posix", "needs a POSIX sh")
class OposClockTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "root"
        (self.root / "opt/opos").mkdir(parents=True)
        (self.tmp / "bin").mkdir()
        date = self.tmp / "bin/date"
        date.write_text(FAKE_DATE)
        date.chmod(0o755)
        self.floor(FLOOR)
        self.now(0)

    def floor(self, value):
        (self.root / "opt/opos/CLOCK_FLOOR").write_text("%s\n" % value)

    def now(self, value):
        (self.tmp / "now").write_text("%d\n" % value)
        (self.tmp / "sets").write_text("")

    def clock(self):
        return int((self.tmp / "now").read_text())

    def sets(self):
        return [int(x) for x in (self.tmp / "sets").read_text().split()]

    def run_clock(self, *args, wait="0", **extra_env):
        env = dict(os.environ, FAKE=str(self.tmp), OPOS_CLOCK_ROOT=str(self.root), OPOS_CLOCK_WAIT=wait,
                   PATH=str(self.tmp / "bin") + os.pathsep + os.environ["PATH"], **extra_env)
        p = subprocess.run([SH, str(PKG / "opos-clock"), *args], env=env, capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout + p.stderr

    def path(self, rel):
        return self.root / rel

    def guess(self):
        return self.path("run/opos/clock-floor").exists()

    def keep(self):
        return self.path("var/lib/opos/clock-guess").exists()

    def touch(self, rel, mtime=None):
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        if mtime is not None:
            os.utime(p, (mtime, mtime))

    # floor

    def test_an_unset_clock_is_raised_to_the_floor_and_marked_a_guess(self):
        rc, out = self.run_clock("floor")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.clock(), FLOOR)
        self.assertEqual(self.sets(), [FLOOR])
        self.assertTrue(self.guess())
        self.assertIn("raised to it", out)

    def test_a_clock_that_cannot_be_set_leaves_no_guess_marker_and_fails(self):
        rc, out = self.run_clock("floor", FAKE_FAIL_SET="1")
        self.assertEqual(rc, 1, out)
        self.assertIn("cannot set the clock", out)
        self.assertFalse(self.guess())
        self.assertEqual(self.clock(), 0)

    def test_a_set_that_fails_says_so_and_changes_nothing(self):
        f = self.drop("%d" % (FLOOR + 1))
        rc, out = self.run_clock("set", str(f), FAKE_FAIL_SET="1")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.clock(), 0)

    def test_a_clock_past_the_floor_is_left_alone(self):
        for value in (FLOOR, FLOOR + 1, FLOOR + 10 ** 6):
            self.now(value)
            rc, out = self.run_clock("floor")
            self.assertEqual(rc, 0, out)
            self.assertEqual(self.clock(), value)
            self.assertEqual(self.sets(), [], "never lowered, never touched")
            self.assertFalse(self.guess())

    def test_the_floor_is_never_earlier_than_2026(self):
        for content in ("", "garbage", "12345", "-5", str(MIN - 1)):
            self.floor(content)
            self.now(1000)
            self.run_clock("floor")
            self.assertEqual(self.clock(), MIN, content)
        (self.root / "opt/opos/CLOCK_FLOOR").unlink()
        self.now(1000)
        self.run_clock("floor")
        self.assertEqual(self.clock(), MIN)

    def test_the_last_clock_systemd_timesyncd_saved_is_a_better_floor(self):
        self.touch("var/lib/systemd/timesync/clock", FLOOR + 3600)
        self.run_clock("floor")
        self.assertEqual(self.clock(), FLOOR + 3600)

    def test_a_saved_clock_far_in_the_future_is_not_believed(self):
        self.touch("var/lib/systemd/timesync/clock", FLOOR + SPAN + 1)
        self.run_clock("floor")
        self.assertEqual(self.clock(), FLOOR)
        self.touch("var/lib/systemd/timesync/clock", FLOOR - 5)
        self.now(0)
        self.run_clock("floor")
        self.assertEqual(self.clock(), FLOOR, "a saved clock below the floor does not lower it")

    def test_the_journal_says_what_the_ports_clock_services_found(self):
        self.touch("mnt/vendor/persist/time/ats_1")
        (self.root / "data/time").mkdir(parents=True)
        (self.root / "data/time/timekeep").write_text("-1\n")
        _, out = self.run_clock("floor")
        self.assertIn("persist ats_1 present", out)
        self.assertIn("timekeep '-1'", out)
        shutil.rmtree(self.root / "mnt")
        _, out = self.run_clock("floor")
        self.assertIn("persist ats_1 absent", out)

    def test_a_guess_survives_a_clean_shutdown_but_not_network_time(self):
        self.run_clock("floor")
        self.run_clock("stop")
        self.assertTrue(self.keep())
        shutil.rmtree(self.root / "run")  # the next boot: /run is empty, timekeeper restored a clock past the floor
        self.now(FLOOR + 500)
        rc, out = self.run_clock("floor")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.guess(), out)
        self.assertEqual(self.sets(), [])
        shutil.rmtree(self.root / "run")
        self.touch("run/systemd/timesync/synchronized")
        self.run_clock("floor")
        self.assertFalse(self.guess())
        self.assertFalse(self.keep())

    def test_network_time_before_the_floor_unit_leaves_nothing_to_guess(self):
        self.touch("var/lib/opos/clock-guess")
        self.touch("run/systemd/timesync/synchronized")
        self.now(FLOOR + 100)
        self.run_clock("floor")
        self.assertFalse(self.guess())
        self.assertFalse(self.keep())

    def test_stop_keeps_a_guess_only_while_it_is_one(self):
        self.run_clock("stop")
        self.assertFalse(self.keep())
        self.touch("run/opos/clock-floor")
        self.run_clock("stop")
        self.assertTrue(self.keep())
        self.touch("run/systemd/timesync/synchronized")
        self.run_clock("stop")
        self.assertFalse(self.keep())

    def test_usage(self):
        for args in ((), ("bogus",)):
            rc, out = self.run_clock(*args)
            self.assertEqual(rc, 2)
            self.assertIn("usage", out)

    # set

    def drop(self, content, name="opos-set-time"):
        p = self.tmp / name
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)
        return p

    def test_a_time_file_sets_the_clock_and_ends_the_guess(self):
        self.run_clock("floor")
        self.run_clock("stop")
        want = FLOOR + 86400
        f = self.drop("%d\n" % want)
        self.now(FLOOR)
        rc, out = self.run_clock("set", str(f))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sets(), [want])
        self.assertFalse(f.exists())
        self.assertFalse(self.guess())
        self.assertFalse(self.keep())
        self.assertIn("now %d" % want, out)

    def test_surrounding_whitespace_is_fine(self):
        f = self.drop("  %d \r\n" % (FLOOR + 5))
        self.assertEqual(self.run_clock("set", str(f))[0], 0)
        self.assertEqual(self.clock(), FLOOR + 5)

    def test_the_bounds_are_the_floor_and_twenty_years_past_it(self):
        self.now(FLOOR)
        for value in (FLOOR - 1, 0, FLOOR + SPAN + 1):
            f = self.drop("%d" % value)
            rc, out = self.run_clock("set", str(f))
            self.assertEqual(rc, 1, (value, out))
            self.assertIn("refused", out)
            self.assertFalse(f.exists(), "a refused file is not left to be retried")
        self.assertEqual(self.sets(), [])
        for value in (FLOOR, FLOOR + SPAN):
            self.assertEqual(self.run_clock("set", str(self.drop("%d" % value)))[0], 0)
            self.assertEqual(self.clock(), value)

    def test_anything_but_digits_is_refused_and_never_echoed(self):
        self.now(FLOOR)
        for content in ("secret-token", "0x6a0000000", "-1794093790", "1794093790.5", "17940937900000000", "1794 093790",
                        "@1794093790", b"\xff\xfe\x00", "date -s now"):
            f = self.drop(content)
            rc, out = self.run_clock("set", str(f))
            self.assertEqual(rc, 1, (content, out))
            self.assertNotIn("secret", out)
            self.assertNotIn("date -s", out)
        self.assertEqual(self.sets(), [])

    def test_an_empty_file_is_refused_after_the_wait(self):
        f = self.drop("")
        rc, out = self.run_clock("set", str(f), wait="0")
        self.assertEqual(rc, 1)
        self.assertEqual(self.sets(), [])

    def test_a_writer_that_finishes_inside_the_wait_is_read(self):
        f = self.drop("")
        env = dict(os.environ, FAKE=str(self.tmp), OPOS_CLOCK_ROOT=str(self.root), OPOS_CLOCK_WAIT="5",
                   PATH=str(self.tmp / "bin") + os.pathsep + os.environ["PATH"])
        p = subprocess.Popen([SH, str(PKG / "opos-clock"), "set", str(f)], env=env, stdout=subprocess.PIPE, text=True)
        time.sleep(0.3)
        f.write_text("%d\n" % (FLOOR + 7))
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, out)
        self.assertEqual(self.clock(), FLOOR + 7)

    def test_a_symlink_is_refused_and_not_followed(self):
        target = self.drop("%d" % (FLOOR + 9), name="target")
        link = self.tmp / "opos-set-time"
        link.symlink_to(target)
        rc, out = self.run_clock("set", str(link))
        self.assertEqual(rc, 1, out)
        self.assertFalse(link.is_symlink())
        self.assertTrue(target.exists())
        self.assertEqual(self.sets(), [])

    def test_a_directory_is_refused(self):
        d = self.tmp / "opos-set-time"
        d.mkdir()
        rc, _ = self.run_clock("set", str(d))
        self.assertEqual(rc, 1)
        self.assertTrue(d.is_dir())
        self.assertEqual(self.sets(), [])

    def test_no_file_is_not_an_error(self):
        rc, out = self.run_clock("set", str(self.tmp / "opos-set-time"))
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(self.sets(), [])

    def test_set_needs_a_file_argument(self):
        self.assertEqual(self.run_clock("set")[0], 2)


class ImageFilesTests(unittest.TestCase):
    def read(self, name):
        return (PKG / name).read_text(encoding="utf-8")

    def test_files_start_with_the_licence_line_and_use_lf(self):
        for name in ("opos-clock", "opos-clock.service", "opos-clock-set.path", "opos-clock-set.service"):
            raw = (PKG / name).read_bytes()
            self.assertNotIn(b"\r", raw, name)
            head = raw.decode().splitlines()[:2]
            self.assertIn("# SPDX-License-Identifier: GPL-3.0-or-later", head, name)
        self.assertTrue(self.read("opos-clock").startswith("#!/bin/sh\n"))

    @unittest.skipUnless(SH, "needs sh")
    def test_scripts_parse(self):
        for name in ("opos-clock", "build-image.sh", "verify-image.sh"):
            p = subprocess.run([SH, "-n", str(PKG / name)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, name + ": " + p.stderr)

    def test_the_clock_unit_has_no_early_ordering(self):
        unit = self.read("opos-clock.service")
        self.assertRegex(unit, r"(?m)^After=timekeeper\.service item-clock-seed\.service$")
        self.assertRegex(unit, r"(?m)^ExecStart=/usr/libexec/opos/opos-clock floor$")
        self.assertRegex(unit, r"(?m)^ExecStop=/usr/libexec/opos/opos-clock stop$")
        self.assertRegex(unit, r"(?m)^RemainAfterExit=yes$")
        self.assertRegex(unit, r"(?m)^WantedBy=multi-user\.target$")
        for line in re.findall(r"(?m)^Before=(.*)$", unit):
            for target in line.split():
                self.assertNotRegex(target, r"sysinit|basic|time-set|time-sync|timesyncd|network", "a cycle on the port once")
        self.assertNotIn("Requires=", unit)
        self.assertNotIn("Conflicts=", unit)

    def test_the_drop_units_name_one_file_through_one_placeholder(self):
        path, service = self.read("opos-clock-set.path"), self.read("opos-clock-set.service")
        self.assertRegex(path, r"(?m)^PathChanged=@DROP@$")
        self.assertRegex(service, r"(?m)^ExecStart=/usr/libexec/opos/opos-clock set @DROP@$")
        self.assertNotIn("/home/", path + service)

    def test_the_drop_is_documented_with_the_same_file_name(self):
        build = self.read("build-image.sh")
        readme = self.read("README.md")
        self.assertIn("$clock_home/opos-set-time", build)
        self.assertIn("~/opos-set-time", readme)
        self.assertIn("mv ~/.opos-time ~/opos-set-time", readme)
        self.assertIn("OPOS_DEV_SSH_PUBKEY", readme.split("## The clock")[1])

    def test_the_readme_warns_of_a_stuck_guess_as_a_reading_and_says_how_to_recover(self):
        clock = " ".join(self.read("README.md").split("## The clock")[1].split("\n## ")[0].split())
        bullet = clock[clock.index("A clean shutdown can make a wrong guess stick"):].split(" - ")[0]
        self.assertIn("reading of the two unit files, not measured", bullet)
        for word in ("`timekeeper`", "`item-clock-seed`", "`ats_1`", "`/data/time/timekeep`", "`opos-clock` cannot undo"):
            self.assertIn(word, bullet)
        self.assertIn("sudo sh -c 'echo -1 > /data/time/timekeep'", bullet)
        self.assertIn("grep 'evidence: persist'", bullet)
        self.assertIn("before any shutdown", bullet)
        self.assertIn("the `timekeep` trap and its recovery", clock.split("Not yet checked on a phone:")[1])

    def test_the_blocks_are_delimited_in_both_scripts(self):
        for name in ("build-image.sh", "verify-image.sh"):
            text = self.read(name)
            self.assertEqual(len(re.findall(r"(?m)^# --- clock: BEGIN", text)), 1, name)
            self.assertEqual(len(re.findall(r"(?m)^# --- clock: END$", text)), 1, name)
            self.assertLess(text.index("# --- clock: BEGIN"), text.index("# --- clock: END"))

    def test_the_build_never_runs_the_clock_script_on_the_builder(self):
        for name in ("build-image.sh", "verify-image.sh"):
            for line in block(name, "clock").splitlines():
                if line.lstrip().startswith(("#", "check ")):
                    continue  # comments, and strings the checks grep for
                self.assertNotRegex(line, r"opos-clock +(floor|stop|set)\b", "would set the builder's own clock")
                self.assertNotRegex(line, r"\bdate +(-u +)?-s", name)


@unittest.skipUnless(SH and GIT and os.name == "posix", "needs sh and git")
class ImageBlocksTests(unittest.TestCase):
    """The clock blocks of build-image.sh and verify-image.sh run against a scratch rootfs tree and a scratch repository."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def repo(self, when):
        repo = self.tmp / "repo"
        shutil.rmtree(repo, ignore_errors=True)
        repo.mkdir()
        env = dict(os.environ, GIT_AUTHOR_DATE="%d +0000" % when, GIT_COMMITTER_DATE="%d +0000" % when)
        for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                                      "commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run([GIT, "-C", str(repo), *args], env=env, check=True, capture_output=True)
        return repo

    def rootfs(self, home="/srv/droidian"):
        r = self.tmp / "rootfs"
        shutil.rmtree(r, ignore_errors=True)
        for d in ("etc/systemd/system/multi-user.target.wants", "etc/systemd/system/sysinit.target.wants", "opt/opos", "usr/libexec/opos"):
            (r / d).mkdir(parents=True)
        (r / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/sh\ndroidian:x:32011:32011::%s:/bin/bash\n" % home)
        (r / "etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service").symlink_to("/usr/lib/systemd/system/systemd-timesyncd.service")
        return r

    def build(self, r, repo, dev_key=None, ref="HEAD"):
        script = self.tmp / "build-block.sh"
        script.write_text("set -eu\n%s\n" % block("build-image.sh", "clock"))
        env = dict(os.environ)
        env.pop("OPOS_DEV_SSH_PUBKEY", None)
        if dev_key:
            env["OPOS_DEV_SSH_PUBKEY"] = dev_key
        prelude = ('r=%s; here=%s; ref=%s; src=%s\n'
                   'g() { git -c safe.directory="$src" -C "$src" "$@"; }\n') % (r, PKG, ref, repo)
        full = self.tmp / "build-run.sh"
        full.write_text(prelude + ". %s\n" % script)
        p = subprocess.run([SH, str(full)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def verify_run(self, r):
        script = self.tmp / "verify-run.sh"
        script.write_text('fail=0; r=%s; w=$r/etc/systemd/system\n'
                          'check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }\n%s\n' % (r, block("verify-image.sh", "clock")))
        return subprocess.run([SH, str(script)], capture_output=True, text=True, timeout=60)

    def verify(self, r):
        p = self.verify_run(r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def verify_fail(self, r):
        return self.verify_run(r).stdout

    def now_minus(self, seconds):
        return int(time.time()) - seconds

    def test_the_floor_is_the_commit_time(self):
        when = max(MIN, self.now_minus(3600))
        r = self.rootfs()
        out = self.build(r, self.repo(when))
        self.assertEqual((r / "opt/opos/CLOCK_FLOOR").read_text(), "%d\n" % when)
        self.assertIn("clock floor %d" % when, out)
        self.assertTrue((r / "usr/libexec/opos/opos-clock").is_file())
        self.assertTrue(os.access(r / "usr/libexec/opos/opos-clock", os.X_OK))
        self.assertEqual(os.readlink(r / "etc/systemd/system/multi-user.target.wants/opos-clock.service"), "/etc/systemd/system/opos-clock.service")
        self.assertEqual((r / "etc/systemd/system/opos-clock.service").read_text(), (PKG / "opos-clock.service").read_text())

    def test_an_annotated_tag_gives_its_commit_time(self):
        when = max(MIN, self.now_minus(7200))
        repo = self.repo(when)
        subprocess.run([GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "tag.gpgsign=false",
                        "tag", "-a", "-m", "release", "v1"], check=True, capture_output=True,
                       env=dict(os.environ, GIT_COMMITTER_DATE="%d +0000" % (when + 5)))
        r = self.rootfs()
        self.build(r, repo, ref="v1")
        self.assertEqual((r / "opt/opos/CLOCK_FLOOR").read_text(), "%d\n" % when)

    def test_a_commit_before_2026_gives_the_2026_floor(self):
        r = self.rootfs()
        self.build(r, self.repo(1577836800))  # 2020-01-01
        self.assertEqual((r / "opt/opos/CLOCK_FLOOR").read_text(), "%d\n" % MIN)

    def test_the_drop_units_are_filled_in_for_the_droidian_home(self):
        r = self.rootfs(home="/srv/droidian")
        self.build(r, self.repo(max(MIN, self.now_minus(60))))
        path = (r / "etc/systemd/system/opos-clock-set.path").read_text()
        service = (r / "etc/systemd/system/opos-clock-set.service").read_text()
        self.assertIn("PathChanged=/srv/droidian/opos-set-time\n", path)
        self.assertIn("ExecStart=/usr/libexec/opos/opos-clock set /srv/droidian/opos-set-time\n", service)
        self.assertNotIn("@DROP@", path + service)
        r = self.rootfs(home="/var/lib/droid")
        self.build(r, self.repo(max(MIN, self.now_minus(60))))
        self.assertIn("PathChanged=/var/lib/droid/opos-set-time\n", (r / "etc/systemd/system/opos-clock-set.path").read_text())

    def test_the_drop_is_enabled_only_in_a_dev_image(self):
        link = "etc/systemd/system/multi-user.target.wants/opos-clock-set.path"
        r = self.rootfs()
        self.build(r, self.repo(max(MIN, self.now_minus(60))))
        self.assertFalse((r / link).is_symlink())
        self.assertTrue((r / "etc/systemd/system/opos-clock-set.path").is_file(), "installed, not enabled")
        r = self.rootfs()
        out = self.build(r, self.repo(max(MIN, self.now_minus(60))), dev_key="/dev/null")
        self.assertEqual(os.readlink(r / link), "/etc/systemd/system/opos-clock-set.path")
        self.assertIn("DEV IMAGE", out)

    def test_the_verify_block_passes_what_the_build_block_makes(self):
        for dev in (False, True):
            r = self.rootfs()
            self.build(r, self.repo(max(MIN, self.now_minus(60))), dev_key="/dev/null" if dev else None)
            if dev:
                (r / "srv/droidian/.ssh").mkdir(parents=True)
                (r / "srv/droidian/.ssh/authorized_keys").write_text("ssh-ed25519 AAAA test\n")
            out = self.verify(r)
            self.assertNotIn("FAIL", out)
            self.assertEqual(out.count("PASS "), 6, out)

    def test_the_verify_block_fails_what_is_wrong(self):
        r = self.rootfs()
        self.build(r, self.repo(max(MIN, self.now_minus(60))))
        floor = r / "opt/opos/CLOCK_FLOOR"
        floor.write_text("%d\n" % (self.now_minus(0) + 30 * 86400))
        self.assertIn("FAIL clock floor recorded", self.verify_fail(r))
        floor.write_text("%d\n" % (MIN - 1))
        self.assertIn("FAIL clock floor recorded", self.verify_fail(r))
        floor.write_text("%d\n" % self.now_minus(60))
        (r / "etc/systemd/system/opos-clock.service").write_text("[Unit]\nBefore=time-set.target\nAfter=timekeeper.service item-clock-seed.service\n")
        self.assertIn("FAIL opos-clock has no Before= on sysinit", self.verify_fail(r))
        (r / "etc/systemd/system/opos-clock.service").write_text((PKG / "opos-clock.service").read_text())
        (r / "etc/systemd/system/systemd-timesyncd.service").symlink_to("/dev/null")
        self.assertIn("FAIL network time still takes over", self.verify_fail(r))
        (r / "etc/systemd/system/systemd-timesyncd.service").unlink()
        (r / "etc/systemd/system/opos-clock-set.service").write_text("ExecStart=/usr/libexec/opos/opos-clock set @DROP@\n")
        self.assertIn("FAIL the time-file drop is filled in", self.verify_fail(r))

    def test_a_dev_image_without_the_drop_enabled_fails_verification(self):
        r = self.rootfs()
        self.build(r, self.repo(max(MIN, self.now_minus(60))))
        (r / "srv/droidian/.ssh").mkdir(parents=True)
        (r / "srv/droidian/.ssh/authorized_keys").write_text("ssh-ed25519 AAAA test\n")
        self.assertIn("FAIL DEV image: the time-file drop is enabled", self.verify_fail(r))


if __name__ == "__main__":
    unittest.main()
