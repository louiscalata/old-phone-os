# SPDX-License-Identifier: GPL-3.0-or-later
"""Back mode: folded all the way back with the lid open, the shell uses one screen and darkens the other.

The pure decisions (layout.py, posture.py), the Blanker's one-panel blank in a fake sysfs tree, the row moves in ui.py
with stand-in boxes, and the shell's wiring on the stand-in window of test_native_shell. No toolkit, no device."""

import ast
import os
import tempfile
import types
import unittest
from pathlib import Path

from opos.native import layout, posture
from tests.test_native_blank import Tree, _broken
from tests.test_native_shell import SHELL, FakeBlanker, StandInShell

POSIX = hasattr(os, "getuid")  # the saved file needs a private runtime folder, which Windows cannot show


class Decisions(unittest.TestCase):
    W, GAP = 1392, 42  # 675 + 42 + 675

    def test_which_screen_a_point_is_on(self):
        at = lambda x: layout.screen_at(x, self.W, self.GAP)
        self.assertEqual([at(x) for x in (0, 674, 675, 700, 716, 717, 1391, 1392)], [0, 0, None, None, None, 1, 1, 1])
        for bad in (-1, 1393, None, True, float("nan"), "3"):
            self.assertIsNone(at(bad), bad)
        self.assertIsNone(layout.screen_at(10, 40, 42), "no room for a screen")
        self.assertEqual((layout.screen_at(10, 800), layout.screen_at(400, 800)), (0, 1), "no hinge: halves")

    def test_the_sensor_verdict_wins_over_a_touch_and_the_thumb_side(self):
        self.assertEqual(layout.active_screen(0, "right", "right"), 1)
        self.assertEqual(layout.active_screen(1, "left", "left"), 0)
        self.assertEqual(layout.active_screen(0, "right", None), 0)
        self.assertEqual(layout.active_screen(None, "right", "x"), 1, "anything else is no verdict")

    def test_the_active_screen_is_the_last_touched_else_the_thumb_side(self):
        self.assertEqual((layout.thumb_screen("right"), layout.thumb_screen("left")), (1, 0))
        self.assertEqual(layout.active_screen(None, "right"), 1)
        self.assertEqual(layout.active_screen(None, "left"), 0)
        self.assertEqual(layout.active_screen(0, "right"), 0)
        self.assertEqual(layout.active_screen(1, "left"), 1)
        self.assertEqual(layout.active_screen("x", "left"), 0, "anything else is not a screen")

    def test_the_panel_to_darken_is_the_other_one(self):
        self.assertEqual((layout.panel_to_darken(0), layout.panel_to_darken(1)), (1, 0))
        self.assertIsNone(layout.panel_to_darken(None))

    def test_touches_off_the_active_screen_are_ignored_only_in_back_mode(self):
        ign = lambda start, active: layout.touch_ignored(start, self.W, self.GAP, active)
        self.assertFalse(ign((1000, 300), 1))
        self.assertTrue(ign((100, 300), 1), "on the dark screen")
        self.assertTrue(ign((690, 300), 1), "in the gap")
        self.assertTrue(ign(None, 1), "unknown start")
        self.assertFalse(ign((100, 300), 0))
        for start in ((100, 300), (1000, 300), None):
            self.assertFalse(ign(start, None), "two screens: nothing is ignored")

    def test_one_screen_only_when_back_with_the_lid_open(self):
        self.assertTrue(posture.one_screen("back", False))
        for name, lid in (
            ("back", None),
            ("back", True),
            ("tent", False),
            ("open", False),
            ("closed", True),
            (None, False),
        ):
            self.assertFalse(posture.one_screen(name, lid), (name, lid))

    def test_back_from_the_angle(self):
        self.assertEqual(posture.posture(False, 350), "back")
        self.assertEqual(posture.posture(False, None, "back"), "open", "the sensor lost: not back")
        self.assertEqual(posture.posture(True, 350), "closed")
        self.assertIn("other is dark", posture.describe("back", 352))

    def test_folded_back_without_back_mode_the_settings_line_says_both_are_lit(self):
        self.assertEqual(posture.describe("back", 352, False), "Back, hinge at 352 degrees. Both screens are lit.")
        self.assertEqual(
            posture.describe("back", 352, True), "Back, hinge at 352 degrees. One screen is lit; the other is dark."
        )
        self.assertEqual(
            posture.describe("tent", 250, True), "Tent, hinge at 250 degrees.", "only back mode darkens one"
        )


class SettingsLine(unittest.TestCase):
    """The Settings Hinge line on the screens' stand-in toolkit: it says one screen is dark only while one is."""

    from tests.test_native_screens import Screens as _Screens

    setUp = _Screens.setUp

    def test_the_line_follows_back_mode_not_the_angle_alone(self):
        shell = self.shell
        shell.posture, shell.angle, shell.back = "back", 352, None  # folded back, the lid unknown: both lit
        s = self.screens.Settings(shell)
        self.assertEqual(s.hinge_line.get_label(), "Back, hinge at 352 degrees. Both screens are lit.")
        shell.back = 1
        s.paint_posture()
        self.assertEqual(s.hinge_line.get_label(), "Back, hinge at 352 degrees. One screen is lit; the other is dark.")
        shell.posture, shell.angle, shell.back = "open", None, None
        s.paint_posture()
        self.assertTrue(s.hinge_line.get_label().startswith("No hinge reading"))


@unittest.skipUnless(POSIX, "needs a private runtime folder (Linux and the phone have one)")
class OnePanel(unittest.TestCase):
    """blank(only=...) in a fake sysfs: one panel dark, the other lit, both saved first, restore lights both."""

    def test_one_panel_dark_both_saved_and_restore_lights_both(self):
        t = Tree(self)
        self.assertTrue(t.blanker.blank(only=(1,)))
        self.assertEqual(t.values(), (90, 0))
        self.assertEqual(t.saved.read_text().split(), ["90", "200"], "both saved before any write")
        self.assertEqual((t.blanker.blanks, t.blanker.failed), (0, []), "not a request for dark screens")
        self.assertTrue(t.blanker.blank(only=(1,)) is False and t.values() == (90, 0), "a repeat writes nothing")
        t.blanker.restore()
        self.assertEqual((t.values(), t.saved.exists()), ((90, 200), False))

    def test_a_saved_file_already_there_is_kept_and_the_other_panel_is_lit_from_it(self):
        t = Tree(self)
        t.blanker.blank()  # the lid's blank, not yet restored
        self.assertEqual(t.values(), (0, 0))
        before = t.saved.read_bytes()
        t.blanker.blank(only=(1,))
        self.assertEqual((t.values(), t.saved.read_bytes()), ((90, 0), before), "never replaced; panel0 lit as saved")
        t.blanker.restore()
        self.assertEqual(t.values(), (90, 200))

    def test_the_lid_blank_after_a_one_panel_blank_darkens_both_and_keeps_the_first_values(self):
        t = Tree(self)
        t.blanker.blank(only=(0,))
        self.assertEqual(t.values(), (0, 200))
        self.assertTrue(t.blanker.blank())
        self.assertEqual((t.values(), t.saved.read_text().split()), ((0, 0), ["90", "200"]))
        t.blanker.restore()
        self.assertEqual(t.values(), (90, 200))

    def test_a_panel_that_cannot_be_lit_is_listed_and_lit_on_a_later_try(self):
        t = Tree(self)
        t.blanker.blank()
        _broken(t, "panel0-backlight")
        t.blanker.blank(only=(1,))
        self.assertTrue(t.blanker.failed and t.saved.exists(), "listed, so the shell tries again")
        p = t.brightness("panel0-backlight")
        p.rmdir()
        p.write_text("0\n")
        t.blanker.blank(only=(1,))
        self.assertEqual((t.values(), t.blanker.failed), ((90, 0), []))

    def test_switching_screens_never_darkens_the_one_in_use_when_the_other_cannot_be_lit(self):
        t = Tree(self)
        t.blanker.blank(only=(1,))  # the left screen in use, the right one dark: (90, 0)
        _broken(t, "panel1-backlight")  # and now the right one cannot be written
        self.assertFalse(t.blanker.blank(only=(0,)), "asked to switch to the right screen: nothing darkened")
        self.assertEqual(t.brightness("panel0-backlight").read_text().strip(), "90", "the screen in use stays lit")
        self.assertTrue(t.blanker.failed and t.saved.exists(), "listed, so the shell tries again")

    def test_a_failed_restore_after_back_mode_keeps_the_file_and_is_retried(self):
        t = Tree(self)
        t.blanker.blank(only=(1,))
        _broken(t, "panel1-backlight")
        t.blanker.restore()
        self.assertTrue(t.blanker.failed and t.saved.exists() and t.blanker.pending())
        p = t.brightness("panel1-backlight")
        p.rmdir()
        p.write_text("0\n")
        t.blanker.restore()
        self.assertEqual((t.values(), t.saved.exists(), t.blanker.failed), ((90, 200), False, []))

    def test_never_both_and_never_without_a_private_folder(self):
        t = Tree(self)
        for bad in ((0, 1), (), (7,), "x"):
            self.assertFalse(t.blanker.blank(only=bad), bad)
        self.assertEqual((t.values(), t.saved.exists()), ((90, 200), False))
        t.run.chmod(0o777)
        self.assertFalse(t.blanker.blank(only=(1,)))
        self.assertEqual((t.values(), t.saved.exists()), ((90, 200), False), "nothing saved, so nothing darkened")

    def test_the_daemons_guard_does_not_stop_it_but_still_stops_the_full_blank(self):
        t = Tree(self)
        t.blanker.may_blank = lambda: False  # the lid reads open
        self.assertFalse(t.blanker.blank())
        self.assertTrue(t.blanker.blank(only=(0,)))
        self.assertEqual(t.values(), (0, 200))


class StandInBox:
    """A Gtk.Box that only keeps its children and how it stretches."""

    def __init__(self, *a, **k):
        self.children, self.parent, self.vx, self.vx_set, self.vh = [], None, False, False, True

    def get_parent(self):
        return self.parent

    def append(self, w):
        assert w.parent is None, "a widget has one parent"
        w.parent = self
        self.children.append(w)

    def remove(self, w):
        self.children.remove(w)
        w.parent = None

    def get_vexpand(self):
        return self.vx

    def get_vexpand_set(self):
        return self.vx_set

    def set_vexpand(self, v):
        self.vx, self.vx_set = v, True

    def set_vexpand_set(self, v):
        self.vx_set = v


class StandInStack(StandInBox):
    def get_vhomogeneous(self):
        return self.vh

    def set_vhomogeneous(self, v):
        self.vh = v


class Rows(StandInShell):
    """ui.one_column and ui.two_columns move the same two panes and put them back exactly."""

    def setUp(self):
        super().setUp()
        import importlib

        ui = self.ui = importlib.import_module("opos.native.ui")
        ui.Gtk.Box, ui.Gtk.Stack = StandInBox, StandInStack

    def row(self, hand):
        row, read, act = StandInBox(), StandInBox(), StandInStack()
        row.read, row.act = read, act
        for name in layout.pane_order(hand):
            row.append(getattr(row, name))
        return row

    def test_one_column_on_either_screen_and_back(self):
        for hand in ("right", "left"):
            for screen in (0, 1):
                row = self.row(hand)
                before = list(row.children)
                self.ui.one_column(row, screen)
                halves = row.children
                self.assertEqual(halves[screen], row.column)
                self.assertEqual(halves[1 - screen], row.empty)
                self.assertEqual(row.column.children, [row.read, row.act], "reading on top, thumb pane below")
                self.assertEqual((row.read.vx, row.act.vx, row.act.vh), (True, False, False))
                column, empty = row.column, row.empty
                self.ui.two_columns(row, hand)
                self.assertEqual(row.children, before, "the same widgets, in the hand's order")
                self.assertEqual((row.read.vx_set, row.act.vx_set, row.act.vh), (False, False, True), "as they were")
                self.assertEqual((column.children, column.parent, empty.parent), ([], None, None))
                self.ui.one_column(row, screen)
                self.assertIs(row.column, column, "made once and kept, nothing new each time")
                self.ui.two_columns(row, hand)

    def test_two_columns_after_a_half_done_one_column_and_on_two_panes(self):
        row = self.row("right")
        self.ui.two_columns(row, "right")
        self.assertEqual(row.children, [row.read, row.act])
        row.column, row.empty = StandInBox(), StandInBox()
        row.remove(row.read)
        row.column.append(row.read)  # stopped half way
        self.ui.two_columns(row, "right")
        self.assertEqual((row.children, row.column.children), ([row.read, row.act], []))


class ShellBack(StandInShell):
    """The shell's back mode on the stand-in window: enter, leave, restore on every way out, touches on the dark side."""

    def back(self, angle=352):
        w = self.win
        w.lid_changed(False)
        self.calls.clear()
        w.hinge_changed(angle)

    def setUp(self):
        super().setUp()

        class OnePanelBlanker(FakeBlanker):
            """Also records blank(only=...): one panel dark on purpose, not counted in `blanks`, as the real one."""

            def blank(self, only=None):
                if only is None:
                    return FakeBlanker.blank(self)
                self.calls.append(("blank", only))
                self.dark = True
                return True

        self.win.blanker = OnePanelBlanker(self.calls)

    def test_folded_back_uses_the_thumb_side_and_darkens_the_other(self):
        w = self.win
        self.back()
        self.assertEqual(w.back, 1, "right-handed and nothing touched: the right screen")
        self.assertEqual(self.calls, [("blank", (0,))])
        self.assertEqual(self.layouts, [("one", "page", 1), ("one", "sheet", 1)])
        self.assertEqual((w.gone, w.hands, w.page), ([], [], "lock"), "the angle never changes the page or the hand")

    def test_the_last_touched_screen_is_used(self):
        w = self.win
        w.lid_changed(False)
        self.touch(200, 400, [])  # a tap on the left screen
        w.hinge_changed(352)
        self.assertEqual((w.back, self.calls[-1]), (0, ("blank", (1,))))

    def test_left_handed_with_nothing_touched_uses_the_left_screen(self):
        w = self.win
        w.hand = "left"
        self.back()
        self.assertEqual((w.back, self.calls), (0, [("blank", (1,))]))

    def test_unfolding_restores_both_and_the_two_panes(self):
        w = self.win
        self.back()
        self.calls.clear()
        self.layouts.clear()
        w.hinge_changed(180)
        self.assertEqual(
            (w.back, self.calls, self.layouts),
            (None, ["restore"], [("two", "page", "right"), ("two", "sheet", "right")]),
        )

    def test_the_sensor_lost_or_any_other_posture_leaves(self):
        for angle in (None, 200, 0, -5):
            with self.subTest(angle=angle):
                self.back()
                self.calls.clear()
                self.win.hinge_changed(angle)
                self.assertEqual((self.win.back, self.calls), (None, ["restore"]))

    def test_a_wobble_inside_back_changes_nothing(self):
        w = self.win
        self.back()
        self.calls.clear()
        for a in (349, 360, 341):  # 341 is inside the hysteresis
            w.hinge_changed(a)
        self.assertEqual((w.back, self.calls), (1, []))

    def test_closing_the_lid_hands_over_to_the_lid_blank_and_opening_comes_back(self):
        w = self.win
        self.back()
        self.calls.clear()
        w.lid_changed(True)
        self.assertEqual((w.back, self.calls, w.gone), (None, [], ["lock"]), "no flash of light before the lid's blank")
        self.fire_timers()
        self.assertEqual(self.calls, ["blank"])
        self.calls.clear()
        w.lid_changed(False)
        self.assertEqual(self.calls, ["restore", ("blank", (0,))], "the lid's restore, then back mode again")
        self.assertEqual(w.back, 1)

    def test_a_lost_lid_signal_leaves_and_lights_both(self):
        w = self.win
        self.back()
        self.calls.clear()
        w.lid_lost()
        self.assertEqual((w.back, set(self.calls)), (None, {"restore"}))

    def test_sigterm_lights_both(self):
        w = self.win
        self.back()
        self.calls.clear()
        w.end()
        self.assertEqual(self.calls, ["restore"])

    def test_an_unknown_lid_never_enters_back_mode(self):
        w = self.win
        w.hinge_changed(352)
        self.assertEqual((w.posture, w.back, self.calls), ("back", None, []))

    def test_panes_that_cannot_move_stay_two_and_both_are_lit(self):
        w = self.win

        def broken(row, screen):
            raise RuntimeError("no")

        self.shell.one_column = broken
        self.back()
        self.assertEqual((w.back, self.calls), (None, ["restore"]))
        self.assertEqual([k for k, _r, _h in self.layouts], ["two", "two"])

    def test_a_darkening_that_raises_lights_both(self):
        w = self.win

        def boom(only=None):
            raise OSError("no")

        w.blanker.blank = boom
        self.back()
        self.assertEqual((w.back, self.calls), (1, ["restore"]))

    def test_the_daemons_restore_in_back_mode_keeps_one_dark(self):
        w = self.win
        self.back()
        self.calls.clear()
        w.restore()
        self.assertEqual(self.calls, [("blank", (0,))])

    def test_a_panel_that_cannot_be_lit_in_back_mode_is_tried_again_and_the_dark_file_alone_is_no_reason(self):
        w = self.win
        self.back()
        self.assertEqual(self.timers, [], "one screen dark on purpose: nothing to retry")
        w.blanker.failed = ["panel1-backlight: busy"]
        w.restore()
        self.assertEqual(len(self.timers), 1)
        self.calls.clear()
        ms, fn, a = self.timers.pop(0)
        w.blanker.failed = []
        fn(*a)
        self.assertEqual((ms, self.calls, self.timers), (1000, [("blank", (0,))], []))

    def test_a_touch_on_the_dark_screen_is_claimed_and_does_nothing(self):
        w = self.win
        w.page = "home"
        self.back()
        self.touch(200, 10, [(0, 40), (0, 300)], end_vy=2000)  # would open the sheet on the active side
        self.assertEqual((self.g.state, w.owned, w.sheet.revealed, w.drag), ("claimed", True, False, None))
        self.g.state = None
        self.touch(200, 450, [(13, 0), (300, 0)], end_vx=900)
        self.assertEqual((w.hands, w.gone), ([], []))
        self.assertEqual(w.last_screen, None, "a touch on the dark side does not move back mode")

    def test_a_drag_from_the_active_screen_may_finish_over_the_dark_one(self):
        w = self.win
        w.page = "home"
        self.back()
        self.touch(1000, 10, [(0, 40), (-700, 300)], pause_ms=400)  # down and across onto the dark screen
        self.assertEqual((w.sheet.revealed, w.ignored), (True, False))

    def test_no_hand_swipe_across_the_hinge_in_back_mode(self):
        w = self.win
        self.back()
        self.touch(1392 / 2 + 40, 400, [(13, 2), (200, 30)], end_vx=500)
        self.assertEqual(w.hands, [])

    def test_the_accelerometer_picks_the_screen_facing_the_user_and_moves_it_when_the_phone_is_flipped(self):
        w = self.win
        clock = [100.0]
        self.shell.time = types.SimpleNamespace(monotonic=lambda: clock[0])
        w.lid_changed(False)
        self.touch(200, 400, [])  # the left screen was touched last (a grip touch, say)
        for z in (900, 900, 900):  # but the right screen is the one facing up
            w.tilt_changed(z)
            clock[0] += 0.5
        w.hinge_changed(352)
        self.assertEqual((w.back, self.calls[-1]), (1, ("blank", (0,))), "the sensor wins over the last touch")
        self.calls.clear()
        for z in (-900, -900, -900):  # flipped over: the left screen faces up now
            w.tilt_changed(z)
            clock[0] += 0.5
        self.assertEqual((w.back, self.calls[-1]), (0, ("blank", (1,))))
        w.tilt_changed(None)  # the sensor lost: it stays where it is
        self.assertEqual(w.back, 0)

    def test_a_tilt_reading_keeps_the_time_the_watcher_thread_took_it(self):
        """The hold time is measured between the readings, not between the main loop's turns to them."""
        from unittest import mock

        shell, w = self.shell, self.win
        clock, queued, seen = [100.0], [], {}
        shell.time = types.SimpleNamespace(monotonic=lambda: clock[0])

        class Capture:
            def __init__(self, on_angle, on_tilt=None, **k):
                seen["on_tilt"] = on_tilt

            def start(self):
                return self

        with (
            mock.patch.object(shell.hinge, "HingeWatcher", Capture),
            mock.patch.object(shell.posture, "find_lid", lambda *a: None),
            mock.patch.object(shell.displayconfig, "own", lambda b, say: None),
            mock.patch.object(shell.GLib, "idle_add", lambda fn, *a: queued.append((fn, a))),
        ):
            w.start_lid()
            self.touch(200, 400, [])  # the left screen in use
            w.lid_changed(False)
            w.hinge_changed(352)
            self.assertEqual(w.back, 0)
            queued.clear()
            for _ in range(3):  # the watcher thread: the right screen faces up, 0.5 s apart
                seen["on_tilt"](900)
                clock[0] += 0.5
            clock[0] += 5.0  # the main loop stalled; it gets to all three at once
            for fn, a in queued:
                fn(*a)
        self.assertEqual(w.back, 1, "held for a second by the readings' own times: moved")

    def test_an_unclear_reading_keeps_the_last_touched_screen(self):
        w = self.win
        w.lid_changed(False)
        self.touch(200, 400, [])
        w.tilt_changed(50)  # held upright: cannot tell
        w.hinge_changed(352)
        self.assertEqual(w.back, 0)

    def test_in_back_mode_both_side_edges_of_the_screen_in_use_go_home(self):
        from opos.native.layout import swipe_action

        W, H, HG = 1392, 1800, 42
        pane = (W - HG) / 2
        for active, xs in ((1, (pane + HG + 3, W - 3)), (0, (3, pane - 3))):
            for x in xs:
                with self.subTest(active=active, x=x):
                    self.assertEqual(swipe_action("app", False, (x, 400), W, H, 500, 0, HG, active), "home")
                    self.assertIsNone(
                        swipe_action("home", False, (x, 400), W, H, 500, 0, HG, active), "only on an app page"
                    )
        self.assertIsNone(
            swipe_action("app", False, (pane + HG + 337, 400), W, H, 500, 0, HG, 1),
            "the middle of the screen is no edge",
        )
        self.assertEqual(
            swipe_action("app", False, (W / 2, 400), W, H, 500, 0, HG, None), "hand-right", "two screens as before"
        )

    def test_a_failed_move_is_tried_once_not_on_every_reading(self):
        w = self.win
        tries = []

        def broken(row, screen):
            tries.append(screen)
            raise RuntimeError("no")

        self.shell.one_column = broken
        w.lid_changed(False)
        for angle in (352, 355, 358, 360):
            w.hinge_changed(angle)
        self.assertEqual(len(tries), 1, "one attempt (the page row fails first), not one per reading")
        w.hinge_changed(120)  # leaving back clears it
        tries.clear()
        w.hinge_changed(352)
        self.assertEqual(len(tries), 1)

    def test_the_hand_setting_in_back_mode_moves_no_pane(self):
        w = self.win
        self.back()
        w.pages = {
            "settings": types.SimpleNamespace(paint_hand=lambda: None),
            "home": types.SimpleNamespace(show=lambda snap: None),
        }
        w.snap = None
        self.shell.save_hand = lambda hand: None
        self.shell.Shell.set_hand(w, "left", save=False)  # the rows are plain strings: touching one would raise
        self.assertEqual(w.hand, "left")

    def test_a_hinge_watcher_that_cannot_start_leaves_the_lid_and_both_screens(self):
        """No thread can be made, say: one log line, the lid watcher stays the one started, nothing is darkened."""
        import io
        from unittest import mock

        shell, w = self.shell, self.win

        class Broken:
            def __init__(self, *a, **k):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            node = Path(d) / "event5"
            node.write_bytes(b"")
            with (
                mock.patch.object(shell.hinge, "HingeWatcher", Broken),
                mock.patch.object(shell.posture, "find_lid", lambda *a: str(node)),
                mock.patch.object(shell.posture, "lid_state", lambda fd: False),
                mock.patch.object(
                    shell.posture, "LidWatcher", lambda *a: types.SimpleNamespace(start=lambda: "watcher")
                ),
                mock.patch.object(shell.displayconfig, "own", lambda b, say: None),
                mock.patch("sys.stderr", err),
            ):
                w.start_lid()
        self.assertEqual((w.lid_watcher, w.hinge_watcher, w.back), ("watcher", None, None))
        self.assertNotIn("blank", [c if isinstance(c, str) else c[0] for c in self.calls])
        self.assertIn("cannot start the hinge watcher (RuntimeError)", err.getvalue())

    def test_a_zero_angle_with_the_lid_open_is_open_not_closed(self):
        """The trap: a hinge reading of 0 (a sensor that has not reported, or a lid short of its switch) while the lid
        reads open must never be "closed", which would darken or lock nothing but would name the phone wrongly."""
        w = self.win
        w.lid_changed(False)
        self.calls.clear()
        for angle in (0, 3, 14.9):
            w.hinge_changed(angle)
            self.assertEqual((w.posture, w.back, self.calls, w.gone), ("open", None, [], []), angle)
        w.lid_changed(True)
        self.assertEqual(w.posture, "closed", "the lid switch says closed")
        w.lid_changed(False)
        w.hinge_changed(0)
        self.assertEqual(w.posture, "open", "after a closed lid the held zone does not bring it back")

    def test_the_hand_swipe_still_works_on_two_screens(self):
        self.win.lid_changed(False)
        self.touch(1392 / 2 - 60, 400, [(13, 2), (200, 30)], end_vx=-500)
        self.assertEqual(self.win.hands, ["left"])


class Source(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(SHELL.read_text(encoding="utf-8"))
        shell = next(n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "Shell")
        self.methods = {n.name: n for n in shell.body if isinstance(n, ast.FunctionDef)}

    def calls(self, name):
        return [ast.unparse(c) for c in ast.walk(self.methods[name]) if isinstance(c, ast.Call)]

    def test_the_blanker_darkens_only_from_the_lid_timer_and_from_relight_one_panel_at_a_time(self):
        where = {n for n, m in self.methods.items() if any(c.startswith("self.blanker.blank(") for c in self.calls(n))}
        self.assertEqual(where, {"blank_if_closed", "relight"})
        self.assertEqual(
            [c for c in self.calls("relight") if c.startswith("self.blanker.blank(")],
            ["self.blanker.blank(only=(panel_to_darken(self.back),))"],
        )

    def test_the_panes_are_moved_only_under_a_guard(self):
        for name, fn in (("enter_back", "one_column("), ("two_rows", "two_columns(")):
            tries = [n for n in ast.walk(self.methods[name]) if isinstance(n, ast.Try)]
            self.assertTrue(tries and any(fn in ast.unparse(t.body[0]) for t in tries), name)

    def test_back_mode_never_locks_changes_the_page_or_the_hand(self):
        for name in ("hinge_changed", "set_posture", "fit_back", "enter_back", "leave_back", "two_rows", "relight"):
            called = [c.split("(")[0] for c in self.calls(name)]
            for bad in ("self.go", "self.set_shade", "self.set_hand", "save_hand", "self.leave"):
                self.assertNotIn(bad, called, name)

    def test_every_end_still_lights_both(self):
        self.assertIn("self.blanker.restore()", self.calls("end"))
        self.assertIn("self.blanker.restore()", self.calls("relight"))


if __name__ == "__main__":
    unittest.main()
