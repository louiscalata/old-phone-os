# SPDX-License-Identifier: GPL-3.0-or-later
"""Both screens dark while the lid is closed, and exactly as bright as before once it opens. No toolkit.

On the Surface Duo 1 running Droidian each panel has a backlight under /sys/class/backlight, and the shell's user may
write its `brightness` file. Blanking writes 0 to both, after saving what they read to a small file in the user's
runtime folder; restoring writes the saved values back and removes the file. Only those two brightness files and the
saved file are ever written; nothing else under /sys is touched (each panel's max_brightness is read, never written),
and nothing is ever read from debugfs. Nothing here starts a program, so nothing here can restart the compositor, the
session or the phone.

The phone must never be left dark:
  - the values are saved (and the file is on disk) before any panel is written, and a saved file is never replaced, so
    a second blank, another shell, or a crash between the two steps cannot leave zeros as the "saved" brightness;
  - a restored value is never 0 (at least LOWEST, at most the max_brightness read from that panel; there is no fixed
    upper clamp), and a malformed saved file restores DEFAULTS;
  - a panel that is missing or cannot be written while restoring counts as failed: the saved file is kept and `failed`
    says so, so the caller tries again (the shell keeps trying while the lid is not closed);
  - a blank that finds the saved file already there (a crash after saving, before writing; a panel that came back on
    while the lid stayed closed) writes 0 again to any panel that reads above 0, and never touches the saved values;
  - a restore with no saved file still lights any panel that reads 0, with its default, and lists a panel it cannot
    read (or that is missing) as failed, since it may be dark;
  - the saved file lives only in a private runtime folder (absolute path, a real folder owned by this user, not writable
    by group or others) and is read without following a symlink; otherwise nothing is blanked at all;
  - the shell restores when the lid opens, when it starts with the lid not closed, when its lid signal is lost, when the
    lid daemon asks (PowerSaveMode 0, through on_restore, so the shell's retries cover it), and when it ends (also on
    SIGTERM); the session launcher runs `python3 -m opos.native.blank --restore` after every end of the shell, clean
    (0) and SIGTERM (143) included, and before the browser starts, and the browser kiosk runs it before the browser starts, which covers
    a shell that was killed outright. That command tries for a few seconds and exits 1 when it could not light both
    panels; the kiosk then runs `--restore --for 120` in the background.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import time

PANELS = ("panel0-backlight", "panel1-backlight")
DEFAULTS = (90, 200)        # what the two panels read on the phone measured; used when nothing better is known
SAVED_NAME = "opos-saved-brightness"
LOWEST = 1                  # a restored value is at least this, so a restore never leaves a panel dark
LONGEST = 6                 # digits: a saved value longer than this is not a brightness, and the file counts as malformed
CLI_WAIT_S = 5.0            # how long `--restore` keeps trying while a panel cannot be written
LATER_MAX_S = 600           # `--restore --for SECONDS` tries for at most this long
LATER_STEP_S = 10.0         # and, after the first few tries, once every this many seconds


def _say(line):
    print("opos blank: " + line, file=sys.stderr, flush=True)


def runtime_dir(env=None):
    """$XDG_RUNTIME_DIR, or /run/user/<uid> when it is not set."""
    env = os.environ if env is None else env
    return env.get("XDG_RUNTIME_DIR") or "/run/user/%d" % getattr(os, "getuid", lambda: 0)()


def unsafe_dir(path):
    """Why path cannot hold the saved file, or "" when it can: it must be absolute, a real folder (not a symlink),
    owned by this user and not writable by group or others, as a systemd runtime folder is."""
    if not path or not os.path.isabs(path):
        return "the runtime folder is not an absolute path"
    try:
        st = os.lstat(path)
    except OSError as err:
        return "no runtime folder %s (%s)" % (path, err.strerror or err)
    if not stat.S_ISDIR(st.st_mode):
        return "%s is not a folder" % path
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return "%s belongs to another user" % path
    if st.st_mode & 0o022:
        return "%s is writable by others" % path
    return ""


def parse_saved(text):
    """The two saved values from the saved file's text, each at least LOWEST; DEFAULTS if malformed. There is no fixed
    upper clamp: Blanker.lit caps a value at the max_brightness read from its own panel."""
    parts = (text or "").split()
    try:
        values = [int(p) for p in parts if len(p.lstrip("+-")) <= LONGEST]
    except ValueError:
        values = []
    if len(values) != len(PANELS) or len(parts) != len(PANELS):
        values = list(DEFAULTS)
    return tuple(max(LOWEST, v) for v in values)


class Blanker:
    """Blanks and restores both panels. root prefixes every sysfs path (tests use a fake tree); run_dir holds the saved
    file. may_blank() is asked before every blank (the shell says no while its lid switch reads open). Never raises for
    a file it cannot read or write: it says so once per call and carries on. After restore(), `failed` lists what could
    not be written, a missing or unreadable panel included (empty when every panel was lit as asked). `blanks` counts
    the deliberate blanks: every blank() that was allowed and found the screens meant to be dark (the saved file in
    place, or every readable panel already at 0), whether or not it had to write 0 again, so a caller retrying a restore
    can tell that someone has asked for dark since. on_restore, when set, takes the place of restore() for a
    PowerSaveMode of 0, so the owner's retry loop covers the lid daemon's restores too (the shell sets it)."""

    def __init__(self, root="/", run_dir=None, say=_say, may_blank=None):
        self.root, self.say = root, say
        self.run_dir = run_dir if run_dir is not None else runtime_dir()
        self.may_blank = may_blank or (lambda: True)
        self.mode = 0       # the last accepted PowerSaveMode: 0 on, 1 to 3 off
        self.failed = []
        self.blanks = 0
        self.on_restore = None

    @property
    def saved_path(self):
        return os.path.join(self.run_dir, SAVED_NAME)

    def panel_dir(self, panel):
        return os.path.join(self.root, "sys", "class", "backlight", panel)

    def brightness_path(self, panel):
        return os.path.join(self.panel_dir(panel), "brightness")

    def _number(self, path):
        try:
            with open(path, encoding="ascii", errors="replace") as f:
                return int(f.read(32).strip())
        except (OSError, ValueError):
            return None

    def read(self, panel):
        """The panel's brightness, or None when it is missing or cannot be read."""
        return self._number(self.brightness_path(panel))

    def lit(self, panel, value):
        """value as a brightness that lights the panel: at least LOWEST, at most the panel's max_brightness (read, never
        written) when that can be read, so a restore is never refused for being too bright."""
        top = self._number(os.path.join(self.panel_dir(panel), "max_brightness"))
        value = max(LOWEST, value)
        return min(value, top) if top and top >= LOWEST else value

    def pending(self):
        """Whether the screens may still be dark from a blank: the saved file is there, or the last restore failed."""
        return os.path.lexists(self.saved_path) or bool(self.failed)

    def _write(self, panel, value, failed):
        """Write value to the panel's brightness. A missing panel (perhaps gone for a moment) is not written (None) and
        is noted in `failed`, as is a write that fails (False)."""
        if not os.path.isdir(self.panel_dir(panel)):
            failed.append("%s: missing" % panel)
            return None
        try:
            with open(self.brightness_path(panel), "w", encoding="ascii") as f:
                f.write("%d\n" % value)
            return True
        except OSError as err:
            failed.append("%s: %s" % (panel, err.strerror or err))
            return False

    def _save(self, values):
        """Write the saved file, mode 0600, and put it in place only if there is none (a hard link never replaces).
        Returns False when a saved file is already there; raises OSError when it cannot be written."""
        fd, tmp = tempfile.mkstemp(prefix="." + SAVED_NAME + ".", dir=self.run_dir)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(" ".join(str(v) for v in values) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            try:
                os.link(tmp, self.saved_path)
            except FileExistsError:
                return False
            return True
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _read_saved(self):
        """The saved file's text; "" when it cannot be read (a symlink is never followed)."""
        try:
            fd = os.open(self.saved_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            return ""
        try:
            return os.read(fd, 64).decode("ascii", "replace")
        except OSError:
            return ""
        finally:
            os.close(fd)

    def blank(self):
        """Save both panels' brightness, then write 0 to both. Returns True when it wrote 0 to a panel. Does nothing
        (False) when blanking is not allowed now, when the runtime folder is not private, or when every readable panel
        is already dark. A panel that reads 0 or cannot be read is saved as its default.

        When a saved file is already there, it is never replaced (it must never be overwritten with zeros or with
        values read after a blank): the panels it covers are already meant to be dark, so any panel that reads above 0
        now (a crash after saving and before writing, or a panel that came back on while the lid stayed closed) is
        written 0 again."""
        if not self.may_blank():
            return False
        why = unsafe_dir(self.run_dir)
        if why:
            self.say(why + "; the screens stay lit")
            return False
        now = [self.read(p) for p in PANELS]
        if os.path.lexists(self.saved_path):
            if os.path.islink(self.saved_path) or not os.path.isfile(self.saved_path):
                return False        # not a saved file this module wrote: never write through it, never darken on it
            self.blanks += 1        # dark was asked for and is on file: deliberate, even when every panel reads 0 already
            return self._darken([p for p, v in zip(PANELS, now) if v])
        if not any(v for v in now if v is not None):
            self.blanks += 1        # already dark, and dark is what was asked for
            return False
        keep = tuple(v if v else d for v, d in zip(now, DEFAULTS))
        try:
            if not self._save(keep):
                return False
        except OSError as err:
            self.say("cannot save the brightness in %s (%s); the screens stay lit" % (self.run_dir, err.strerror or err))
            return False
        self.blanks += 1
        self._darken(PANELS)
        return True

    def _darken(self, panels):
        """Write 0 to each of panels; True when at least one was written."""
        failed, wrote = [], False
        for p in panels:
            wrote = self._write(p, 0, failed) is True or wrote
        if failed:
            self.say("cannot blank " + "; ".join(failed))
        return wrote

    def restore(self):
        """Write the saved brightness back and remove the saved file; with no saved file, light any panel that reads
        0 with its default. Returns True when anything was written. A failed write, or a panel that is missing while a
        saved file is there, keeps the file for a later try and is listed in self.failed."""
        failed, wrote = [], False
        if os.path.lexists(self.saved_path):
            for p, v in zip(PANELS, parse_saved(self._read_saved())):
                wrote = self._write(p, self.lit(p, v), failed) is True or wrote
            if not failed:
                try:
                    os.unlink(self.saved_path)
                except OSError as err:
                    failed.append("cannot remove %s: %s" % (self.saved_path, err.strerror or err))
        else:
            for p, d in zip(PANELS, DEFAULTS):
                now = self.read(p)
                if now == 0:
                    wrote = self._write(p, self.lit(p, d), failed) is True or wrote
                elif now is None:   # it may be dark and cannot be checked: listed, so the CLI and the shell try again
                    failed.append("%s: %s" % (p, "unreadable" if os.path.isdir(self.panel_dir(p)) else "missing"))
        self.failed = failed
        if failed:
            self.say("cannot restore " + "; ".join(failed))
        return wrote

    def set_mode(self, mode):
        """A PowerSaveMode from the display service: 0 restores, 1 to 3 blank, anything else is ignored."""
        if isinstance(mode, bool) or not isinstance(mode, int) or mode not in (0, 1, 2, 3):
            return
        self.mode = mode
        if mode == 0:
            (self.on_restore or self.restore)()
        else:
            self.blank()


def main(argv=None, make=None, sleep=time.sleep, wait_s=CLI_WAIT_S, clock=time.monotonic):
    """`python3 -m opos.native.blank --restore`: light both screens as they were (the launcher and the browser kiosk run
    this before the browser starts). While a panel cannot be written it tries again, for up to CLI_WAIT_S seconds in
    all, measured on the monotonic clock from the first try, so the time spent in the tries counts as well as the sleeps:
    no try starts after that deadline, so the most it can take is the bound plus one try. Exits 0 when both panels were lit (or nothing was dark), 1 when it could not light them; it never raises, and
    its callers run it as `... || log ...` or `... || :`, so a failure never stops the browser.

    `--restore --for SECONDS` (1 to LATER_MAX_S) tries for that long instead, at a low rate (at most one try every
    LATER_STEP_S seconds once the first few have failed). The browser kiosk runs it in the background when the quick
    restore gave up, so the browser is never delayed and the screens are still lit once the panel takes the write."""
    argv = sys.argv[1:] if argv is None else argv
    top = 1.0
    if len(argv) == 3 and argv[:2] == ["--restore", "--for"] and argv[2].isdigit() and 1 <= int(argv[2]) <= LATER_MAX_S:
        wait_s, top, argv = float(argv[2]), LATER_STEP_S, argv[:1]
    if argv != ["--restore"]:
        _say("usage: python3 -m opos.native.blank --restore [--for SECONDS]")
        return 0
    try:
        start = clock()
        deadline, step = start + wait_s, 0.25
        b = (make or Blanker)()
        b.restore()
        while b.failed:
            left = deadline - clock()
            if left <= 0:
                break
            sleep(min(step, left))      # the last sleep is cut to the time left, so the bound is the bound
            step = min(step * 2, top)
            b.restore()
        waited = clock() - start
        if b.failed:
            _say("could not light both screens after %.0f s: %s" % (waited, "; ".join(b.failed)))
            return 1
    except Exception as err:      # never let a restore end the caller's script with a traceback
        _say("restore failed: %r" % (err,))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
