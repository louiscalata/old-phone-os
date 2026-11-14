# SPDX-License-Identifier: GPL-3.0-or-later
"""blank.py and displayconfig.py: blanking and restoring both panels in a fake sysfs tree, the PowerSaveMode state, and
the D-Bus wiring with a stand-in gi. No toolkit, no bus and no real device."""
import ast
import importlib
import os
import stat
import sys
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path

from opos.native import blank, displayconfig

NATIVE = Path(__file__).resolve().parents[1] / "src" / "opos" / "native"


class Tree:
    """A fake root: <root>/sys/class/backlight/<panel>/{brightness,bl_power,max_brightness} and a runtime folder."""

    def __init__(self, test, values=(90, 200), panels=blank.PANELS):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run = self.root / "run" / "user" / "1000"
        self.run.mkdir(parents=True)
        self.run.chmod(0o700)       # as systemd makes $XDG_RUNTIME_DIR, whatever the umask of the test run
        for panel, value in zip(panels, values):
            d = self.root / "sys" / "class" / "backlight" / panel
            d.mkdir(parents=True)
            (d / "brightness").write_text("%d\n" % value, encoding="ascii")
            (d / "bl_power").write_text("0\n", encoding="ascii")
            (d / "max_brightness").write_text("255\n", encoding="ascii")
        self.said = []
        self.blanker = blank.Blanker(root=str(self.root), run_dir=str(self.run), say=self.said.append)

    def brightness(self, panel):
        return (self.root / "sys" / "class" / "backlight" / panel / "brightness")

    def values(self):
        out = []
        for p in blank.PANELS:
            try:
                out.append(int(self.brightness(p).read_text(encoding="ascii")))
            except (OSError, ValueError):
                out.append(None)
        return tuple(out)

    @property
    def saved(self):
        return self.run / blank.SAVED_NAME

    def snapshot(self):
        """Every file under the root with its contents."""
        snap = {}
        for dirpath, _dirs, files in os.walk(self.root):
            for f in files:
                p = Path(dirpath) / f
                snap[p.relative_to(self.root).as_posix()] = p.read_bytes()
        return snap


def _broken(t, panel="panel0-backlight"):
    """Make the panel's brightness unwritable and unreadable: a folder where the file was."""
    p = t.brightness(panel)
    p.unlink()
    p.mkdir()


class Clock:
    """A monotonic clock that moves only when the command sleeps (or when a test says a try took time)."""

    def __init__(self, then=None):
        self.t, self.slept, self.then = 0.0, [], then

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s
        if self.then:
            self.then(s)


class Blanking(unittest.TestCase):
    def test_blank_then_restore_gives_back_each_panels_own_value(self):
        t = Tree(self, (37, 211))
        self.assertTrue(t.blanker.blank())
        self.assertEqual(t.values(), (0, 0))
        self.assertEqual(t.saved.read_text(encoding="ascii").split(), ["37", "211"])
        self.assertTrue(t.blanker.restore())
        self.assertEqual(t.values(), (37, 211))
        self.assertFalse(t.saved.exists())
        self.assertEqual(t.said, [])

    def test_a_second_blank_keeps_the_values_saved_by_the_first_and_darkens_a_panel_that_came_back_on(self):
        t = Tree(self, (90, 200))
        self.assertTrue(t.blanker.blank())
        t.brightness("panel0-backlight").write_text("0\n", encoding="ascii")   # still dark
        before = t.snapshot()
        self.assertFalse(t.blanker.blank(), "both dark: nothing to write")
        self.assertEqual(t.snapshot(), before)
        t.brightness("panel1-backlight").write_text("150\n", encoding="ascii")  # lit while closed
        self.assertTrue(t.blanker.blank(), "dark again")
        self.assertEqual(t.values(), (0, 0))
        self.assertEqual(t.saved.read_text(encoding="ascii").split(), ["90", "200"], "the saved values are never replaced")
        self.assertEqual(t.blanker.blanks, 3, "every allowed blank counts, also the one that had nothing to write")
        t.blanker.restore()
        self.assertEqual(t.values(), (90, 200))

    def test_blank_when_already_dark_does_nothing(self):
        t = Tree(self, (0, 0))
        before = t.snapshot()
        self.assertFalse(t.blanker.blank())
        self.assertEqual(t.snapshot(), before)
        self.assertFalse(t.saved.exists())

    def test_a_dark_or_unreadable_panel_is_saved_as_its_default(self):
        t = Tree(self, (0, 120))
        self.assertTrue(t.blanker.blank())
        self.assertEqual(t.saved.read_text(encoding="ascii").split(), ["90", "120"])
        t.blanker.restore()
        self.assertEqual(t.values(), (90, 120))
        t = Tree(self, (120, 50))
        t.brightness("panel0-backlight").write_text("junk", encoding="ascii")
        t.blanker.blank()
        self.assertEqual(t.saved.read_text(encoding="ascii").split(), ["90", "50"])

    def test_a_malformed_saved_file_falls_back_to_the_defaults(self):
        for text in ("", "junk", "1", "1 2 3", "1 x", "\x00\x01"):
            with self.subTest(text=text):
                t = Tree(self, (0, 0))
                t.saved.write_text(text, encoding="latin-1")
                self.assertTrue(t.blanker.restore())
                self.assertEqual(t.values(), blank.DEFAULTS)
                self.assertFalse(t.saved.exists())

    def test_restored_values_are_clamped(self):
        t = Tree(self, (0, 0))
        t.saved.write_text("0 999", encoding="ascii")
        t.blanker.restore()
        self.assertEqual(t.values(), (1, 255), "at the max_brightness each panel reads (255 here)")
        self.assertEqual(blank.parse_saved("-5 7"), (1, 7))

    def test_a_panel_brighter_than_255_is_restored_exactly(self):
        t = Tree(self, (0, 0))
        for p in blank.PANELS:
            (t.root / "sys/class/backlight" / p / "max_brightness").write_text("4095\n", encoding="ascii")
        t.saved.write_text("1800 4095", encoding="ascii")
        self.assertTrue(t.blanker.restore())
        self.assertEqual(t.values(), (1800, 4095), "no fixed 255 clamp: only the panel's own maximum")
        self.assertEqual(blank.parse_saved("1800 4095"), (1800, 4095))
        self.assertEqual(blank.parse_saved("1234567 5"), blank.DEFAULTS, "a value longer than a brightness is malformed")

    def test_dark_with_no_saved_file_lights_the_dark_panels_with_their_defaults(self):
        t = Tree(self, (0, 140))
        self.assertTrue(t.blanker.restore())
        self.assertEqual(t.values(), (90, 140))
        t = Tree(self, (60, 140))
        before = t.snapshot()
        self.assertFalse(t.blanker.restore(), "nothing dark, nothing written")
        self.assertEqual(t.snapshot(), before)

    def test_a_missing_panel_is_never_created_and_counts_as_failed(self):
        t = Tree(self, (75,), panels=("panel1-backlight",))
        self.assertTrue(t.blanker.blank())
        self.assertEqual(t.values(), (None, 0))
        self.assertEqual(t.saved.read_text(encoding="ascii").split(), ["90", "75"])
        self.assertEqual(len(t.said), 1, "the blank says the panel it could not reach")
        self.assertIn("panel0-backlight: missing", t.said[0])
        self.assertTrue(t.blanker.restore())
        self.assertEqual(t.values(), (None, 75))
        self.assertFalse((t.root / "sys" / "class" / "backlight" / "panel0-backlight").exists(), "nothing is created")
        self.assertEqual(t.blanker.failed, ["panel0-backlight: missing"])
        self.assertTrue(t.saved.exists(), "kept: the missing panel may come back dark and must be lit then")
        self.assertTrue(t.blanker.pending())

    def test_a_panel_missing_for_a_moment_during_a_restore_is_lit_when_it_comes_back(self):
        t = Tree(self, (33, 44))
        t.blanker.blank()
        gone = t.root / "sys" / "class" / "backlight" / "panel0-backlight"
        away = t.root / "panel0-away"
        gone.rename(away)                    # the panel's driver is reloading
        t.blanker.restore()
        self.assertEqual((t.values(), t.saved.exists()), ((None, 44), True))
        self.assertTrue(t.blanker.failed)
        away.rename(gone)                    # back, still at 0
        t.blanker.restore()
        self.assertEqual((t.values(), t.saved.exists(), t.blanker.failed), ((33, 44), False, []))
        self.assertFalse(t.blanker.pending())

    def test_no_panels_at_all_does_nothing(self):
        t = Tree(self, (), panels=())
        self.assertFalse(t.blanker.blank())
        self.assertFalse(t.blanker.restore())
        self.assertFalse(t.saved.exists())

    def broken(self, t, panel="panel0-backlight"):
        """Make the panel's brightness unwritable: a folder where the file was."""
        p = t.brightness(panel)
        p.unlink()
        p.mkdir()

    def test_a_write_failure_is_said_once_and_never_raised(self):
        t = Tree(self, (90, 200))
        self.broken(t)
        self.assertTrue(t.blanker.blank())
        self.assertEqual(t.values(), (None, 0))
        self.assertEqual(len(t.said), 1)
        self.assertIn("panel0-backlight", t.said[0])

    def test_a_failed_restore_keeps_the_saved_file_for_a_later_try(self):
        t = Tree(self, (33, 44))
        t.blanker.blank()
        self.broken(t)
        t.said.clear()
        self.assertTrue(t.blanker.restore(), "the other panel was written")
        self.assertEqual(len(t.said), 1)
        self.assertTrue(t.saved.exists())
        self.assertEqual(t.values(), (None, 44))
        t.brightness("panel0-backlight").rmdir()
        t.brightness("panel0-backlight").write_text("0\n", encoding="ascii")
        self.assertTrue(t.blanker.restore())
        self.assertEqual(t.values(), (33, 44))
        self.assertFalse(t.saved.exists())

    def test_no_runtime_folder_means_no_blank(self):
        t = Tree(self, (90, 200))
        t.blanker.run_dir = str(t.run / "missing")
        self.assertFalse(t.blanker.blank())
        self.assertEqual(t.values(), (90, 200), "never dark without the values saved first")
        self.assertEqual(len(t.said), 1)

    def test_modes(self):
        t = Tree(self, (90, 200))
        b = t.blanker
        self.assertEqual(b.mode, 0)
        for mode in (1, 2, 3):
            with self.subTest(mode=mode):
                b.set_mode(mode)
                self.assertEqual((b.mode, t.values()), (mode, (0, 0)))
                b.set_mode(0)
                self.assertEqual((b.mode, t.values()), (0, (90, 200)))
        b.set_mode(3)
        before = t.snapshot()
        for ignored in (4, -1, 99, "3", 3.0, None, True):
            with self.subTest(ignored=ignored):
                b.set_mode(ignored)
                self.assertEqual((b.mode, t.snapshot()), (3, before))
        b.set_mode(3)
        self.assertEqual(t.snapshot(), before, "a re-asserted 3 is harmless")

    def test_only_the_brightness_files_and_the_saved_file_are_written(self):
        t = Tree(self, (90, 200))
        before = t.snapshot()
        allowed = {"sys/class/backlight/%s/brightness" % p for p in blank.PANELS}
        allowed.add((t.saved.relative_to(t.root)).as_posix())
        t.blanker.set_mode(3)
        mid = t.snapshot()
        t.blanker.set_mode(0)
        after = t.snapshot()
        for snap in (mid, after):
            changed = {k for k in set(before) | set(snap) if before.get(k) != snap.get(k)}
            self.assertLessEqual(changed, allowed)
        self.assertEqual(after, before, "everything is back as it was")

    @unittest.skipUnless(os.name == "posix", "file modes are POSIX")
    def test_the_saved_file_is_private(self):
        t = Tree(self, (90, 200))
        old = os.umask(0)
        try:
            t.blanker.blank()
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(t.saved.stat().st_mode), 0o600)

    def test_the_runtime_folder(self):
        self.assertEqual(blank.runtime_dir({"XDG_RUNTIME_DIR": "/x"}), "/x")
        self.assertTrue(blank.runtime_dir({}).startswith("/run/user/"))


class Safety(unittest.TestCase):
    """The paths that could leave a panel dark, or write somewhere they should not (the lid-blanking safety review)."""

    def test_max_brightness_caps_a_restore_so_it_is_never_refused(self):
        t = Tree(self, (0, 0))
        for p in blank.PANELS:
            (t.root / "sys/class/backlight" / p / "max_brightness").write_text("100\n", encoding="ascii")
        t.saved.write_text("90 200\n", encoding="ascii")
        self.assertTrue(t.blanker.restore())
        self.assertEqual(t.values(), (90, 100))
        t.blanker.blank()
        (t.root / "sys/class/backlight/panel1-backlight/max_brightness").write_text("junk", encoding="ascii")
        t.blanker.restore()
        self.assertEqual(t.values(), (90, 100), "an unreadable maximum is not a reason to stay dark")

    def test_a_crash_between_saving_and_writing_leaves_the_screens_lit_and_restorable(self):
        t = Tree(self, (37, 211))
        real = t.blanker._write
        t.blanker._write = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            t.blanker.blank()
        self.assertEqual(t.values(), (37, 211), "nothing was written: lit")
        t.blanker._write = real
        self.assertTrue(t.blanker.blank(), "a restart with the lid closed finishes the blank")
        self.assertEqual(t.values(), (0, 0))
        self.assertEqual(t.saved.read_text(encoding="ascii").split(), ["37", "211"], "the saved file is never replaced")
        t.blanker.restore()
        self.assertEqual((t.values(), t.saved.exists()), ((37, 211), False))

    def test_killed_while_dark_then_restored_from_the_file_alone(self):
        t = Tree(self, (37, 211))
        t.blanker.blank()
        fresh = blank.Blanker(root=str(t.root), run_dir=str(t.run), say=t.said.append)     # a new process
        self.assertTrue(fresh.restore())
        self.assertEqual(t.values(), (37, 211))

    def test_a_saved_file_that_appears_meanwhile_is_never_replaced(self):
        t = Tree(self, (90, 200))
        real = t.blanker._save

        def other_shell_first(values):
            t.saved.write_text("11 22\n", encoding="ascii")      # another shell saved between our check and our save
            return real(values)
        t.blanker._save = other_shell_first
        self.assertFalse(t.blanker.blank())
        self.assertEqual(t.saved.read_text(encoding="ascii").split(), ["11", "22"])
        self.assertEqual(t.values(), (90, 200), "and this one did not darken anything")
        self.assertEqual([f for f in os.listdir(t.run) if f != blank.SAVED_NAME], [], "no temporary file left")

    @unittest.skipUnless(os.name == "posix", "symlinks and modes are POSIX")
    def test_a_symlinked_saved_file_is_not_followed(self):
        t = Tree(self, (0, 0))
        secret = t.root / "elsewhere"
        secret.write_text("250 250\n", encoding="ascii")
        t.saved.symlink_to(secret)
        self.assertTrue(t.blanker.restore())
        self.assertEqual(t.values(), blank.DEFAULTS, "the link is not read")
        self.assertFalse(os.path.lexists(t.saved), "the link is removed")
        self.assertEqual(secret.read_text(encoding="ascii"), "250 250\n", "its target is untouched")
        t.saved.symlink_to(secret)
        self.assertFalse(t.blanker.blank(), "a link where the saved file goes is never written through")
        self.assertEqual(secret.read_text(encoding="ascii"), "250 250\n")

    @unittest.skipUnless(os.name == "posix", "modes are POSIX")
    def test_only_a_private_runtime_folder_holds_the_saved_file(self):
        t = Tree(self, (90, 200))
        t.run.chmod(0o777)
        self.assertFalse(t.blanker.blank())
        t.run.chmod(0o770)
        self.assertFalse(t.blanker.blank())
        self.assertEqual(t.values(), (90, 200), "never dark when the values cannot be kept safely")
        self.assertEqual(len(t.said), 2)
        t.run.chmod(0o700)
        link = t.root / "run-link"
        link.symlink_to(t.run)
        t.blanker.run_dir = str(link)
        self.assertFalse(t.blanker.blank(), "a symlinked folder is refused")
        t.blanker.run_dir = "relative/run"
        self.assertFalse(t.blanker.blank())
        self.assertEqual(t.values(), (90, 200))
        self.assertIn("not an absolute path", blank.unsafe_dir(""))
        t.blanker.run_dir = str(t.run)
        self.assertTrue(t.blanker.blank())

    def test_may_blank_says_no_and_nothing_is_written(self):
        t = Tree(self, (90, 200))
        t.blanker.may_blank = lambda: False
        before = t.snapshot()
        t.blanker.set_mode(3)
        self.assertEqual(t.snapshot(), before)

    def test_a_failed_restore_is_listed_so_the_shell_can_try_again(self):
        t = Tree(self, (33, 44))
        t.blanker.blank()
        p = t.brightness("panel0-backlight")
        p.unlink()
        p.mkdir()
        t.blanker.restore()
        self.assertEqual(len(t.blanker.failed), 1)
        p.rmdir()
        p.write_text("0\n", encoding="ascii")
        t.blanker.restore()
        self.assertEqual((t.blanker.failed, t.values()), ([], (33, 44)))

    def test_the_command_line_restore_never_raises_and_says_whether_it_lit_the_screens(self):
        import contextlib
        import io
        t = Tree(self, (37, 211))
        t.blanker.blank()
        real_init = blank.Blanker.__init__

        def at_tree(self, root="/", run_dir=None, say=blank._say, may_blank=None):
            real_init(self, root=str(t.root), run_dir=str(t.run), say=say, may_blank=may_blank)
        with unittest.mock.patch.object(blank.Blanker, "__init__", at_tree):
            self.assertEqual(blank.main(["--restore"], sleep=self.fail), 0, "lit at the first try: no wait")
        self.assertEqual(t.values(), (37, 211))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(blank.main([]), 0, "a wrong call still exits 0")
            with unittest.mock.patch.object(blank.Blanker, "restore", side_effect=RuntimeError("boom")):
                self.assertEqual(blank.main(["--restore"]), 1, "it could not light them: non-zero, never a traceback")
        self.assertIn("boom", err.getvalue())

    def test_with_no_saved_file_an_unreadable_or_missing_panel_counts_as_failed(self):
        """R6: it may be dark and cannot be checked, so the CLI and the shell must try again."""
        t = Tree(self, (90, 0))
        t.brightness("panel0-backlight").unlink()
        self.assertTrue(t.blanker.restore(), "the dark panel it can read is lit")
        self.assertEqual((t.values(), t.blanker.failed), ((None, 200), ["panel0-backlight: unreadable"]))
        self.assertTrue(t.blanker.pending())
        t.brightness("panel0-backlight").write_text("0\n", encoding="ascii")
        t.blanker.restore()
        self.assertEqual((t.values(), t.blanker.failed, t.blanker.pending()), ((90, 200), [], False))
        gone = Tree(self, (90, 200), panels=("panel0-backlight",))
        gone.blanker.restore()
        self.assertEqual(gone.blanker.failed, ["panel1-backlight: missing"])

    def test_a_blank_on_screens_already_dark_still_counts_as_deliberate(self):
        """R7: the saved file is there and both panels read 0 (or cannot be read). A second blank writes nothing, but it is
        still a request for dark, so a pending restore retry must see the counter move and stop."""
        t = Tree(self, (90, 200))
        self.assertTrue(t.blanker.blank())
        self.assertEqual(t.blanker.blanks, 1)
        self.assertFalse(t.blanker.blank(), "nothing to write")
        self.assertEqual(t.blanker.blanks, 2)
        _broken(t, "panel0-backlight")
        _broken(t, "panel1-backlight")
        t.blanker.blank()
        self.assertEqual(t.blanker.blanks, 3, "unreadable panels with the saved file in place: dark is still what was asked")
        dark = Tree(self, (0, 0))
        dark.blanker.blank()
        self.assertEqual((dark.blanker.blanks, dark.saved.exists()), (1, False), "already dark, nothing saved: still counted")
        refused = Tree(self, (90, 200))
        refused.blanker.may_blank = lambda: False
        refused.blanker.blank()
        self.assertEqual(refused.blanker.blanks, 0, "a refused blank is not one")

    def test_mode_0_goes_through_on_restore_when_set(self):
        """R3: the owner's restore (with its retries) handles the lid daemon's PowerSaveMode 0."""
        t = Tree(self, (90, 200))
        seen = []
        t.blanker.on_restore = lambda: seen.append("hook")
        t.blanker.set_mode(3)
        t.blanker.set_mode(0)
        self.assertEqual((seen, t.values(), t.blanker.mode), (["hook"], (0, 0), 0), "the hook, not a bare restore")
        t.blanker.on_restore = None
        t.blanker.set_mode(0)
        self.assertEqual(t.values(), (90, 200))

    def test_the_command_line_restore_can_keep_trying_slowly_for_longer(self):
        """R4: `--restore --for 120` is the kiosk's background retry: bounded, and at a low rate."""
        import contextlib
        import io
        t = Tree(self, (37, 211))
        t.blanker.blank()
        _broken(t)
        make = lambda: blank.Blanker(root=str(t.root), run_dir=str(t.run), say=lambda _l: None)  # noqa: E731
        clk = Clock()
        slept = clk.slept
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(blank.main(["--restore", "--for", "120"], make=make, sleep=clk.sleep, clock=clk.now), 1)
        self.assertEqual(sum(slept), 120, "round 6: the last sleep is cut to the time left, so 120 s means 120 s")
        self.assertEqual(slept[-1], 120 - sum(slept[:-1]))
        self.assertLessEqual(slept[-1], blank.LATER_STEP_S)
        self.assertEqual(max(slept), blank.LATER_STEP_S)
        self.assertLess(len(slept), 20, "a low rate, not a busy loop")
        p = t.brightness("panel0-backlight")

        def back(_s):
            if len(clk.slept) == 9 and p.is_dir():
                p.rmdir()
                p.write_text("0\n", encoding="ascii")
        clk = Clock(back)
        self.assertEqual(blank.main(["--restore", "--for", "120"], make=make, sleep=clk.sleep, clock=clk.now), 0)
        self.assertEqual((t.values(), t.saved.exists()), ((37, 211), False))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for bad in (["--restore", "--for", "0"], ["--restore", "--for", "601"], ["--restore", "--for", "x"], ["--for", "5"]):
                self.assertEqual(blank.main(bad, make=self.fail), 0, bad)
        self.assertIn("usage", err.getvalue())

    def test_the_command_line_restore_tries_again_for_a_few_seconds(self):
        import contextlib
        import io
        t = Tree(self, (37, 211))
        t.blanker.blank()
        p = t.brightness("panel0-backlight")
        p.unlink()
        p.mkdir()
        def back(_s):           # the panel takes the write after about two seconds
            if clk.t >= 2 and p.is_dir():
                p.rmdir()
                p.write_text("0\n", encoding="ascii")
        clk = Clock(back)
        slept = clk.slept
        make = lambda: blank.Blanker(root=str(t.root), run_dir=str(t.run), say=lambda _l: None)  # noqa: E731
        self.assertEqual(blank.main(["--restore"], make=make, sleep=clk.sleep, clock=clk.now), 0)
        self.assertEqual((t.values(), t.saved.exists()), ((37, 211), False))
        self.assertTrue(all(0 < s <= 1.0 for s in slept), slept)
        t.blanker.blank()
        p.unlink()
        p.mkdir()
        clk = Clock()
        slept = clk.slept
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(blank.main(["--restore"], make=make, sleep=clk.sleep, clock=clk.now), 1, "still dark: says so")
        self.assertEqual(sum(slept), blank.CLI_WAIT_S, "a few seconds, not for ever: the browser must still start")
        self.assertIn("could not light both screens", err.getvalue())
        self.assertTrue(t.saved.exists(), "kept for the next try")

    def test_the_time_spent_in_each_try_counts_towards_the_bound(self):
        """Round 7: the bound is wall time on the monotonic clock, not the sum of the sleeps. With tries that take 0.2 s
        each, a 1 s bound used to take about 1.85 s; now no try starts after the deadline, so it is at most 1 s plus
        one try."""
        import contextlib
        import io
        clk = Clock()
        tries = []

        class Slow:
            failed = ["panel0-backlight: unreadable"]

            def restore(self):
                tries.append(clk.t)
                clk.t += 0.2        # each try takes 0.2 s

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(blank.main(["--restore"], make=Slow, sleep=clk.sleep, clock=clk.now, wait_s=1.0), 1)
        self.assertLess(max(tries), 1.0 + 1e-9, "no try starts after the deadline")
        self.assertLessEqual(clk.t, 1.0 + 0.2 + 1e-9, "the bound plus one try at most")
        self.assertLess(sum(clk.slept), 1.0, "the tries' own time was counted")
        clk.t, clk.slept[:] = 0.0, []
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(blank.main(["--restore", "--for", "30"], make=Slow, sleep=clk.sleep, clock=clk.now), 1)
        self.assertLessEqual(clk.t, 30 + 0.2 + 1e-9)


    def write_only(self, t, panel="panel0-backlight"):
        """Make the panel's brightness writable but not readable (mode 0200), as a sysfs file whose read fails; skip where
        the test runs with the rights to read it anyway (root)."""
        p = t.brightness(panel)
        p.chmod(0o200)
        self.addCleanup(p.chmod, 0o644)
        try:
            p.read_text(encoding="ascii")
        except OSError:
            return p
        self.skipTest("this user can read a mode 0200 file")

    def test_with_no_saved_file_an_unreadable_dark_panel_is_written_and_stays_listed(self):
        """Residual 9 (round 7): with no saved file, a panel at 0 that cannot be read was listed as failed but never written,
        so it stayed dark through every retry (the verifier saw 3 restores and 0 writes). Now a panel whose brightness file
        is there but cannot be read is written its default, capped by its readable max_brightness, and stays listed in
        `failed` until a read confirms it lit, so the retries keep going and the panel is never left dark."""
        t = Tree(self, (0, 140))
        (t.root / "sys/class/backlight/panel0-backlight/max_brightness").write_text("60\n", encoding="ascii")
        p = self.write_only(t)
        for _ in range(3):
            t.blanker.restore()
            self.assertEqual(t.blanker.failed, ["panel0-backlight: unreadable"], "still listed: not confirmed lit")
            self.assertTrue(t.blanker.pending(), "so the shell and the CLI keep trying")
        p.chmod(0o644)
        self.assertEqual(t.values(), (60, 140), "written its default, capped by its own max_brightness: lit, never 0")
        self.assertFalse(t.blanker.restore(), "readable and lit now: nothing to write")
        self.assertEqual((t.blanker.failed, t.blanker.pending()), ([], False))

    def test_with_no_saved_file_an_unreadable_panel_without_a_brightness_file_is_never_written(self):
        """The new write is only to a brightness file that is there: nothing is ever created under /sys."""
        t = Tree(self, (0, 0))
        t.brightness("panel0-backlight").unlink()
        t.blanker.restore()
        self.assertFalse(t.brightness("panel0-backlight").exists(), "nothing is created")
        self.assertEqual(t.blanker.failed, ["panel0-backlight: unreadable"])
        self.assertEqual(t.values(), (None, 200))

    def test_the_cli_lights_an_unreadable_dark_panel_with_no_saved_file(self):
        """The verifier's case through `--restore`: it writes the panel (lit) and still exits 1, since it could not confirm."""
        import contextlib
        import io
        t = Tree(self, (0, 0))
        p = self.write_only(t)
        make = lambda: blank.Blanker(root=str(t.root), run_dir=str(t.run), say=lambda _l: None)  # noqa: E731
        clk = Clock()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(blank.main(["--restore"], make=make, sleep=clk.sleep, clock=clk.now), 1)
        p.chmod(0o644)
        self.assertEqual(t.values(), blank.DEFAULTS, "lit, although it could not be read")

    def test_a_refused_blank_reports_mode_0_and_an_accepted_one_its_mode(self):
        """Residual 2: a PowerSaveMode of 1 to 3 that is refused (the lid reads open) leaves the screens lit, so the mode
        reported is 0, not the refused request. Every request still reaches blank() or restore(), so nothing that lights
        a screen is skipped; the reported value only says whether this request was a blank, and no decision reads it."""
        t = Tree(self, (90, 200))
        lid_open = [True]
        t.blanker.may_blank = lambda: not lid_open[0]
        for mode in (1, 2, 3):
            with self.subTest(refused=mode):
                self.assertEqual(t.blanker.set_mode(mode), 0)
                self.assertEqual((t.blanker.mode, t.values()), (0, (90, 200)), "refused: lit, and it says 0")
        lid_open[0] = False
        self.assertEqual(t.blanker.set_mode(3), 3)
        self.assertEqual((t.blanker.mode, t.values()), (3, (0, 0)), "accepted: dark, and it says 3")
        self.assertEqual(t.blanker.set_mode(3), 3, "a re-asserted 3 on dark screens is still dark")
        lid_open[0] = True
        self.assertEqual(t.blanker.set_mode(3), 0, "a refused 3 while the saved file is there")
        self.assertEqual(t.values(), (0, 0), "a refused blank writes nothing: the lit-again path is restore, not set_mode(3)")
        self.assertEqual(t.blanker.set_mode(0), 0)
        self.assertEqual((t.blanker.mode, t.values()), (0, (90, 200)), "0 still restores")
        t.blanker.run_dir = str(t.run / "missing")
        lid_open[0] = False
        self.assertEqual(t.blanker.set_mode(3), 0, "a blank refused for an unsafe runtime folder: lit, says 0")
        self.assertEqual(t.values(), (90, 200))
        self.assertEqual(t.blanker.set_mode(7), 0, "an ignored value changes nothing and says the mode in force")


class Blanked:
    """A blanker that only records modes; every mode takes effect."""

    def __init__(self):
        self.modes = []

    def set_mode(self, mode):
        self.modes.append(mode)
        return mode


class Refusing(Blanked):
    """A blanker whose lid reads open: every blank is refused, so the mode in force stays 0."""

    def set_mode(self, mode):
        self.modes.append(mode)
        return 0


class PowerSave(unittest.TestCase):
    def test_get_set_and_changed(self):
        b = Blanked()
        s = displayconfig.PowerSaveState(b)
        self.assertEqual((s.get("PowerSaveMode"), s.get("Other")), (0, None))
        self.assertTrue(s.set("PowerSaveMode", 3))
        self.assertEqual(s.get("PowerSaveMode"), 3)
        self.assertFalse(s.set("PowerSaveMode", 3), "a repeat is no change")
        self.assertTrue(s.set("PowerSaveMode", 0))
        self.assertEqual(b.modes, [3, 3, 0], "but a repeat still reaches the blanker")
        self.assertEqual(s.properties_changed_args(), (displayconfig.IFACE, {"PowerSaveMode": 0}, []))

    def test_a_refused_request_is_not_stored_as_the_value(self):
        """Residual 2: while the lid reads open a 3 is refused; the property reads 0 (the screens are lit), no change is
        reported, and the request still reached the blanker."""
        b = Refusing()
        s = displayconfig.PowerSaveState(b)
        self.assertFalse(s.set("PowerSaveMode", 3), "refused: the value did not change")
        self.assertEqual((s.get("PowerSaveMode"), b.modes), (0, [3]))
        self.assertFalse(s.set("PowerSaveMode", 0))
        self.assertEqual(b.modes, [3, 0], "a 0 reaches the blanker even when the value already reads 0")

    def test_a_blanker_that_does_not_say_the_mode_keeps_the_request(self):
        class Silent(Blanked):
            def set_mode(self, mode):
                self.modes.append(mode)
        s = displayconfig.PowerSaveState(Silent())
        self.assertTrue(s.set("PowerSaveMode", 3))
        self.assertEqual(s.get("PowerSaveMode"), 3, "never None: the property always has an int")

    def test_the_value_follows_a_real_blanker_and_the_screens_match_it(self):
        """With a real Blanker in a fake tree: a refused 3 reads 0 with the panels lit, an accepted 3 reads 3 with them
        dark, and a 0 reads 0 with them lit again."""
        t = Tree(self, (90, 200))
        lid_open = [True]
        t.blanker.may_blank = lambda: not lid_open[0]
        s = displayconfig.PowerSaveState(t.blanker)
        self.assertFalse(s.set("PowerSaveMode", 3))
        self.assertEqual((s.get("PowerSaveMode"), t.values()), (0, (90, 200)))
        lid_open[0] = False
        self.assertTrue(s.set("PowerSaveMode", 3))
        self.assertEqual((s.get("PowerSaveMode"), t.values()), (3, (0, 0)))
        self.assertTrue(s.set("PowerSaveMode", 0))
        self.assertEqual((s.get("PowerSaveMode"), t.values()), (0, (90, 200)))

    def test_a_wrong_name_or_type_is_refused(self):
        b = Blanked()
        s = displayconfig.PowerSaveState(b)
        for name, value in (("Other", 3), ("PowerSaveMode", "3"), ("PowerSaveMode", 3.0), ("PowerSaveMode", None),
                            ("PowerSaveMode", True)):
            with self.subTest(name=name, value=value):
                self.assertFalse(s.set(name, value))
        self.assertEqual((b.modes, s.get("PowerSaveMode")), ([], 0))

    def test_the_interface_has_exactly_one_property(self):
        import xml.etree.ElementTree as ET
        node = ET.fromstring(displayconfig.XML)
        ifaces = node.findall("interface")
        self.assertEqual([i.get("name") for i in ifaces], [displayconfig.IFACE])
        self.assertEqual([(p.get("name"), p.get("type"), p.get("access")) for p in ifaces[0]],
                         [("PowerSaveMode", "i", "readwrite")])
        self.assertEqual((displayconfig.IFACE, displayconfig.PATH),
                         ("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig"))


class Variant:
    def __init__(self, sig, value):
        self.sig, self.value = sig, value

    def unpack(self):
        return self.value


class Conn:
    def __init__(self):
        self.objects, self.signals = [], []

    def register_object(self, path, iface, method, get, set_):
        self.objects.append((path, iface, method, get, set_))
        return 1

    def emit_signal(self, *args):
        self.signals.append(args)


class StandInGi(unittest.TestCase):
    """own() with a stand-in gi: the closures it registers, and that it never raises."""

    def gi(self, own=None, new_for_xml=None):
        names = ("gi", "gi.repository")
        saved = {n: sys.modules.get(n) for n in names}

        def restore():
            for n, m in saved.items():
                sys.modules.pop(n, None)
                if m is not None:
                    sys.modules[n] = m
        self.addCleanup(restore)
        gi, repo = types.ModuleType("gi"), types.ModuleType("gi.repository")
        gi.repository = repo
        self.owned = []
        Gio = types.SimpleNamespace(
            BusType=types.SimpleNamespace(SESSION="session"),
            BusNameOwnerFlags=types.SimpleNamespace(ALLOW_REPLACEMENT=1, REPLACE=2, DO_NOT_QUEUE=4),
            DBusNodeInfo=types.SimpleNamespace(new_for_xml=new_for_xml or (lambda xml: types.SimpleNamespace(
                interfaces=["iface from " + xml[:5]]))),
            bus_own_name=own or (lambda *a: self.owned.append(a) or 7))
        repo.Gio, repo.GLib = Gio, types.SimpleNamespace(Variant=Variant)
        sys.modules["gi"], sys.modules["gi.repository"] = gi, repo

    def test_own_takes_the_name_without_replacing_and_serves_the_property(self):
        self.gi()
        said, b = [], Blanked()
        self.assertEqual(displayconfig.own(b, said.append), 7)
        (bus, name, flags, acquired, got, lost), = self.owned
        self.assertEqual((bus, name, flags, got), ("session", displayconfig.IFACE, 4 | 1, None),
                         "never REPLACE, never queue, and a later owner that asks may take it over")
        conn = Conn()
        acquired(conn, name)
        (path, iface, method, get, set_), = conn.objects
        self.assertEqual((path, iface, method), (displayconfig.PATH, "iface from <node", None))
        self.assertEqual(get(conn, ":1.5", path, name, "PowerSaveMode").value, 0)
        self.assertIsNone(get(conn, ":1.5", path, name, "Other"))
        self.assertIs(set_(conn, ":1.5", path, name, "PowerSaveMode", Variant("i", 3)), True)
        self.assertEqual(b.modes, [3])
        (dest, spath, siface, signal, body), = conn.signals
        self.assertEqual((dest, spath, siface, signal, body.sig),
                         (None, displayconfig.PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged", "(sa{sv}as)"))
        iface_name, changed, gone = body.value
        self.assertEqual((iface_name, {k: (v.sig, v.value) for k, v in changed.items()}, gone),
                         (displayconfig.IFACE, {"PowerSaveMode": ("i", 3)}, []))
        self.assertIs(set_(conn, ":1.5", path, name, "PowerSaveMode", Variant("i", 3)), True, "a repeat succeeds")
        self.assertEqual((b.modes, len(conn.signals)), ([3, 3], 1), "but signals nothing")
        self.assertIs(set_(conn, ":1.5", path, name, "Other", Variant("i", 3)), False)
        self.assertEqual(get(conn, ":1.5", path, name, "PowerSaveMode").value, 3)
        lost(None, name)
        lost(None, name)
        self.assertEqual(len(said), 1, "losing the name is said once")

    def test_a_refused_set_answers_true_reads_0_and_signals_nothing(self):
        """Residual 2 on the bus: the daemon's call still succeeds (it is not an error to ask), Get reads 0, and no
        PropertiesChanged says 3."""
        self.gi()
        b = Refusing()
        displayconfig.own(b, lambda _l: None)
        (_bus, name, _flags, acquired, _got, _lost), = self.owned
        conn = Conn()
        acquired(conn, name)
        (path, _iface, _method, get, set_), = conn.objects
        self.assertIs(set_(conn, ":1.5", path, name, "PowerSaveMode", Variant("i", 3)), True)
        self.assertEqual((get(conn, ":1.5", path, name, "PowerSaveMode").value, conn.signals, b.modes), (0, [], [3]))

    def test_a_blanker_that_raises_is_said_once_and_answers_false(self):
        self.gi()
        said = []

        class Bad:
            def set_mode(self, _m):
                raise RuntimeError("boom")
        displayconfig.own(Bad(), said.append)
        conn = Conn()
        self.owned[0][3](conn, displayconfig.IFACE)
        set_ = conn.objects[0][4]
        for _ in range(2):
            self.assertIs(set_(conn, "", displayconfig.PATH, displayconfig.IFACE, "PowerSaveMode", Variant("i", 3)), False)
        self.assertEqual(len(said), 1)

    def test_own_never_raises(self):
        def fail(*_a):
            raise RuntimeError("no bus")
        for kwargs in ({"own": fail}, {"new_for_xml": fail}):
            with self.subTest(kwargs=list(kwargs)):
                self.gi(**kwargs)
                said = []
                self.assertIsNone(displayconfig.own(Blanked(), said.append))
                self.assertEqual(len(said), 1)

    def test_without_gi_it_imports_and_own_says_so_once(self):
        saved = {n: sys.modules.get(n) for n in ("gi", "gi.repository", "opos.native.displayconfig")}

        def restore():
            for n, m in saved.items():
                sys.modules.pop(n, None)
                if m is not None:
                    sys.modules[n] = m
        self.addCleanup(restore)
        sys.modules["gi"] = None            # import gi now fails, as on a machine without PyGObject
        sys.modules.pop("gi.repository", None)
        sys.modules.pop("opos.native.displayconfig", None)
        mod = importlib.import_module("opos.native.displayconfig")
        said = []
        self.assertIsNone(mod.own(Blanked(), said.append))
        self.assertEqual(len(said), 1)


class Source(unittest.TestCase):
    def test_no_gi_at_module_level(self):
        for name in ("blank.py", "displayconfig.py"):
            tree = ast.parse((NATIVE / name).read_text(encoding="utf-8"))
            top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
            mods = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names}
            mods |= {n.module.split(".")[0] for n in top if isinstance(n, ast.ImportFrom) and n.module}
            with self.subTest(name=name):
                self.assertNotIn("gi", mods)

    def test_never_power_or_debugfs(self):
        for name in ("blank.py", "displayconfig.py"):
            text = (NATIVE / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotIn("bl_power", text)
                self.assertNotIn("/sys/kernel/debug", text)
                self.assertNotIn("debug", text.replace("debugfs", ""), "no debug path of any kind")

    def test_only_brightness_is_named_under_sys(self):
        """brightness is written; max_brightness is only read (the write tests show it never changes)."""
        tree = ast.parse((NATIVE / "blank.py").read_text(encoding="utf-8"))
        strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertIn("brightness", strings)
        self.assertFalse({s for s in strings if s in ("bl_power", "actual_brightness", "power")})

    def test_nothing_here_starts_a_program_or_restarts_anything(self):
        for name in ("blank.py", "displayconfig.py", "posture.py"):
            text = (NATIVE / name).read_text(encoding="utf-8")
            tree = ast.parse(text)
            mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            with self.subTest(name=name):
                self.assertFalse(mods & {"subprocess", "socket", "urllib", "ctypes", "signal"}, mods)
                for bad in ("os.system", "os.exec", "os.spawn", "os.kill", "systemctl", "reboot", "loginctl", "Restart",
                            "call_sync", "new_for_bus_sync", "bus_get_sync"):
                    self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
