# SPDX-License-Identifier: GPL-3.0-or-later
"""The native shell: its toolkit-free rules always; the GTK module only where GTK 4 and PyGObject are installed."""
import ast
import re
import tempfile
import unittest
from pathlib import Path

from opos.native import layout

NATIVE = Path(__file__).resolve().parents[1] / "src" / "opos" / "native"
SHELL = NATIVE / "shell.py"


class Hinge(unittest.TestCase):
    def test_the_gap_comes_from_the_span_the_server_reads(self):
        self.assertEqual(layout.hinge_px({"OPOS_SPAN": "675,42"}), 42)
        self.assertEqual(layout.hinge_px({"OPOS_SPAN": " 675 , 0 "}), 0)

    def test_one_screen_or_a_malformed_span_has_no_gap(self):
        for env in ({}, {"OPOS_SPAN": ""}, {"OPOS_SPAN": "675"}, {"OPOS_SPAN": "a,b"}, {"OPOS_SPAN": "675,42,1"}, {"OPOS_SPAN": "-1,42"}):
            with self.subTest(env=env):
                self.assertEqual(layout.hinge_px(env), 0)


class Swipes(unittest.TestCase):
    W, H, HINGE = 1392, 900, 42

    def act(self, page, shade, start, vx, vy):
        return layout.swipe_action(page, shade, start, self.W, self.H, vx, vy, self.HINGE)

    def test_up_on_the_lock_screen_unlocks(self):
        self.assertEqual(self.act("lock", False, (300, 600), 0, -400), "home")
        self.assertIsNone(self.act("lock", False, (300, 600), 0, -layout.FAR), "a slow drag is not a swipe")
        self.assertIsNone(self.act("home", False, (300, 600), 0, -400), "up from the middle of Home does nothing")

    def test_down_from_the_top_edge_opens_the_shade_on_every_page(self):
        for page in ("lock", "home", "app"):
            with self.subTest(page=page):
                self.assertEqual(self.act(page, False, (300, layout.EDGE - 1), 0, 400), "shade-open")
                self.assertIsNone(self.act(page, False, (300, layout.EDGE), 0, 400), "not from the middle of the screen")

    def test_an_open_shade_takes_every_swipe_and_only_up_closes_it(self):
        self.assertEqual(self.act("home", True, (300, 300), 0, -400), "shade-close")
        self.assertIsNone(self.act("lock", True, (300, 300), 400, 0))
        self.assertIsNone(self.act("lock", True, (300, 10), 0, 400), "down under an open shade does not reopen it")
        self.assertEqual(self.act("lock", True, (300, 300), 0, -400), "shade-close", "and does not unlock underneath")
        self.assertIsNone(self.act("home", True, (self.W / 2, 400), 500, 0), "and does not move the buttons underneath")

    def test_up_from_the_bottom_edge_goes_home_from_any_page_but_the_lock(self):
        for page in ("home", "app"):
            with self.subTest(page=page):
                self.assertEqual(self.act(page, False, (300, self.H - 10), 0, -400), "home")
                self.assertIsNone(self.act(page, False, (300, self.H - layout.EDGE), 0, -400), "only from the edge")
        self.assertEqual(self.act("lock", False, (300, self.H - 10), 0, -400), "home", "on the lock screen any swipe up unlocks")

    def test_a_sideways_swipe_from_the_middle_moves_the_buttons_to_that_side(self):
        mid = self.W / 2
        for page in ("lock", "home", "app"):
            with self.subTest(page=page):
                self.assertEqual(self.act(page, False, (mid - 60, 400), 500, 10), "hand-right")
                self.assertEqual(self.act(page, False, (mid + 60, 400), -500, 10), "hand-left")
        edge = self.HINGE / 2 + layout.MIDDLE
        self.assertEqual(self.act("home", False, (mid + edge, 400), 500, 0), "hand-right", "the band's last pixel counts")
        self.assertIsNone(self.act("home", False, (mid + edge + 1, 400), 500, 0), "outside the band nothing moves")
        self.assertIsNone(self.act("home", False, (mid, 400), 100, 500), "a mostly vertical swipe is not sideways")
        self.assertIsNone(self.act("home", False, (mid, 400), layout.FAR, 0), "a slow drag is not a swipe")

    def test_a_side_edge_swipe_leaves_an_app_page_only(self):
        self.assertEqual(self.act("app", False, (10, 400), 500, 20), "home")
        self.assertEqual(self.act("app", False, (self.W - 10, 400), -500, 20), "home")
        self.assertIsNone(self.act("app", False, (300, 400), 500, 20), "not from the middle of a screen")
        self.assertIsNone(self.act("app", False, (10, 400), 100, 500), "a mostly vertical swipe is not a side swipe")
        self.assertIsNone(self.act("home", False, (10, 400), 500, 20))

    def test_one_screen_has_its_middle_band_too(self):
        self.assertEqual(layout.swipe_action("home", False, (360, 400), 720, 1440, 500, 0), "hand-right")
        self.assertEqual(layout.swipe_action("home", False, (360, 400), 720, 1440, -500, 0), "hand-left")

    def test_no_start_point_means_nothing_happens(self):
        self.assertIsNone(self.act("lock", False, None, 0, -400))


def old_swipe_action(page, shade_open, start, width, height, vx, vy, hinge=0):
    """swipe_action as it was before drags followed the finger, copied here so any change to its answers shows."""
    if not start:
        return None
    x, y = start
    down, up = vy > 80, vy < -80
    side = abs(vx) > abs(vy) and abs(vx) > 80
    if shade_open:
        return "shade-close" if up else None
    if down and y < 48:
        return "shade-open"
    if page == "lock" and up:
        return "home"
    if page != "lock" and up and y > height - 48:
        return "home"
    if side and abs(x - width / 2) <= hinge / 2 + 110:
        return "hand-right" if vx > 0 else "hand-left"
    if page == "app" and side and (x < 48 or x > width - 48):
        return "home"
    return None


class SwipesUnchanged(unittest.TestCase):
    def test_every_answer_is_what_it_was(self):
        xs = (0, 10, 47, 48, 300, 585, 586, 696, 806, 807, 1344, 1345, 1391)
        ys = (0, 47, 48, 400, 851, 852, 899)
        vs = (-500, -81, -80, 0, 80, 81, 500)
        n = 0
        for page in ("lock", "home", "app"):
            for shade in (False, True):
                for start in [None] + [(x, y) for x in xs for y in ys]:
                    for vx in vs:
                        for vy in vs:
                            for size in ((1392, 900, 42), (720, 1440, 0)):
                                w, h, g = size
                                self.assertEqual(layout.swipe_action(page, shade, start, w, h, vx, vy, g),
                                                 old_swipe_action(page, shade, start, w, h, vx, vy, g),
                                                 (page, shade, start, vx, vy, size))
                                n += 1
        self.assertGreater(n, 50000)


class Drags(unittest.TestCase):
    W, H = 1392, 900

    def kind(self, page, sheet, start, dx, dy):
        return layout.drag_kind(page, sheet, start, self.W, self.H, dx, dy)

    def test_the_numbers(self):
        self.assertEqual((layout.SLOP, layout.SHEET_FULL, layout.UNLOCK_FULL, layout.HOME_FULL), (12, 0.45, 0.5, 0.4))
        self.assertEqual((layout.SHEET_DONE, layout.UNLOCK_DONE, layout.HOME_DONE), (0.35, 0.4, 0.3))
        self.assertEqual((layout.EDGE, layout.FAR), (48, 80))

    def test_down_from_the_top_edge_opens_the_sheet_on_every_page(self):
        for page in ("lock", "home", "app"):
            for x in (0, 300, self.W / 2, self.W - 1):
                with self.subTest(page=page, x=x):
                    self.assertEqual(self.kind(page, False, (x, layout.EDGE - 1), 0, 13), "sheet-open")
                    self.assertEqual(self.kind(page, False, (x, 0), -13, 13), "sheet-open", "exactly diagonal still counts")

    def test_an_open_sheet_takes_every_upward_drag_and_nothing_else(self):
        for page in ("lock", "home", "app"):
            for start in ((300, 10), (300, 450), (300, self.H - 1)):
                with self.subTest(page=page, start=start):
                    self.assertEqual(self.kind(page, True, start, 0, -13), "sheet-close")
                    self.assertIsNone(self.kind(page, True, start, 0, 13), "down under an open sheet")

    def test_up_on_the_lock_screen_anywhere_unlocks(self):
        for start in ((0, 0), (300, 450), (self.W / 2, 450), (self.W - 1, self.H - 1)):
            with self.subTest(start=start):
                self.assertEqual(self.kind("lock", False, start, 5, -13), "lock")

    def test_up_from_the_bottom_edge_of_an_app_page_goes_home(self):
        self.assertEqual(self.kind("app", False, (300, self.H - layout.EDGE + 1), 0, -13), "home")
        self.assertEqual(self.kind("app", False, (self.W - 1, self.H - 1), 0, -200), "home")

    def test_every_case_with_no_drag(self):
        none = [
            ("lock", False, None, 0, -200, "no start point"),
            ("app", False, None, 0, 200, "no start point"),
            ("lock", False, (300, 450), 0, -layout.SLOP, "not past the slop: may be a tap"),
            ("lock", False, (300, 450), 0, 0, "not moved"),
            ("app", False, (300, 10), 0, layout.SLOP, "not past the slop, from the top edge"),
            ("lock", False, (300, 450), -14, -13, "mostly sideways"),
            ("app", False, (300, 10), 30, 13, "mostly sideways, from the top edge"),
            ("home", False, (300, layout.EDGE), 0, 200, "down, but not from the top edge"),
            ("lock", False, (300, 450), 0, 200, "down on the lock screen, not from the top edge"),
            ("app", False, (300, 10), 0, -200, "up from the top edge of an app page"),
            ("home", False, (300, 450), 0, -200, "up from the middle of Home"),
            ("app", False, (300, 450), 0, -200, "up from the middle of an app page"),
            ("app", False, (300, self.H - layout.EDGE), 0, -200, "not quite the bottom edge"),
            ("home", False, (300, self.H - 1), 0, -200, "up from the bottom edge of Home: already Home"),
            ("app", False, (300, self.H - 1), 0, 200, "down from the bottom edge"),
            ("home", True, (300, 10), 0, 200, "down under an open sheet, even from the top edge"),
            ("lock", True, (300, 450), 0, layout.SLOP + 1, "down under an open sheet does not unlock"),
            ("app", True, (300, 450), 200, -13, "mostly sideways under an open sheet"),
            ("home", True, (300, 450), 0, -layout.SLOP, "not past the slop under an open sheet"),
        ]
        for page, sheet, start, dx, dy, why in none:
            with self.subTest(why=why, page=page):
                self.assertIsNone(self.kind(page, sheet, start, dx, dy), why)

    def test_progress_is_clamped_to_0_and_1(self):
        H = self.H
        cases = [("sheet-open", 0.45 * H, 1.0), ("sheet-open", 0.45 * H / 2, 0.5), ("sheet-open", 5 * H, 1.0),
                 ("sheet-open", -100, 0.0), ("sheet-open", 0, 0.0),
                 ("sheet-close", -0.45 * H, 1.0), ("sheet-close", -0.45 * H / 2, 0.5), ("sheet-close", -5 * H, 1.0),
                 ("sheet-close", 100, 0.0),
                 ("lock", -0.5 * H, 1.0), ("lock", -0.25 * H, 0.5), ("lock", -5 * H, 1.0), ("lock", 100, 0.0),
                 ("home", -0.4 * H, 1.0), ("home", -0.2 * H, 0.5), ("home", -5 * H, 1.0), ("home", 100, 0.0),
                 (None, -500, 0.0), ("nonsense", -500, 0.0)]
        for kind, dy, want in cases:
            with self.subTest(kind=kind, dy=dy):
                self.assertAlmostEqual(layout.drag_progress(kind, dy, H), want)
        self.assertEqual(layout.drag_progress("lock", -100, 0), 0.0, "no window yet")

    def test_release_at_the_thresholds(self):
        for kind, done in (("sheet-open", 0.35), ("sheet-close", 0.35), ("lock", 0.4), ("home", 0.3)):
            with self.subTest(kind=kind):
                self.assertEqual(layout.drag_release(kind, done, 0), "complete", "exactly the threshold")
                self.assertEqual(layout.drag_release(kind, done - 1e-9, 0), "cancel", "just under")
                self.assertEqual(layout.drag_release(kind, 1.0, 0), "complete")
                self.assertEqual(layout.drag_release(kind, 0.0, 0), "cancel")

    def test_a_flick_completes_however_short(self):
        for kind, sign in (("sheet-open", 1), ("sheet-close", -1), ("lock", -1), ("home", -1)):
            with self.subTest(kind=kind):
                self.assertEqual(layout.drag_release(kind, 0.0, sign * (layout.FAR + 1)), "complete")
                self.assertEqual(layout.drag_release(kind, 0.0, sign * layout.FAR), "cancel", "FAR itself is not a flick")
                self.assertEqual(layout.drag_release(kind, 0.1, -sign * 2000), "cancel", "a flick the other way")
        self.assertEqual(layout.drag_release(None, 1.0, -2000), "cancel")

    def test_velocity_from_the_last_moments(self):
        self.assertEqual(layout.drag_velocity([]), 0.0)
        self.assertEqual(layout.drag_velocity([(1000, 0)]), 0.0)
        self.assertAlmostEqual(layout.drag_velocity([(1000, 0), (1100, -100)]), -1000.0)
        self.assertAlmostEqual(layout.drag_velocity([(0, 0), (1000, -500), (1050, -500), (1100, -600)]), -1000.0,
                               "only the last FLICK_MS count")
        self.assertEqual(layout.drag_velocity([(1000, 0), (1100, -300), (1400, -300)]), 0.0, "stopped, then lifted")
        self.assertEqual(layout.drag_velocity([(1000, 0), (1000, -300)]), 0.0, "no time passed")

    def test_offsets_follow_the_finger(self):
        H = self.H
        self.assertAlmostEqual(layout.drag_offset("sheet-open", 0, H), -H, msg="closed sheet: all of it above the top")
        for dy in (13, 90, 100, 405, 700):
            self.assertAlmostEqual(layout.drag_offset("sheet-open", dy, H) - layout.drag_offset("sheet-open", 0, H), dy,
                                   msg="the sheet moves exactly as far as the finger, not 1/SHEET_FULL times as far")
            self.assertAlmostEqual(layout.drag_offset("sheet-close", -dy, H), -dy, msg="closing too")
        self.assertAlmostEqual(layout.drag_offset("sheet-open", 2 * H, H), 0, msg="never past fully shown")
        self.assertAlmostEqual(layout.drag_offset("sheet-close", -2 * H, H), -H, msg="never past fully hidden")
        self.assertAlmostEqual(layout.drag_offset("sheet-close", 100, H), 0)
        for kind in ("lock", "home"):
            self.assertEqual(layout.drag_offset(kind, -123, H), -123, "a page moves with the finger")
            self.assertEqual(layout.drag_offset(kind, 50, H), 0, "never down")
            self.assertEqual(layout.drag_offset(kind, -5 * H, H), -H, "never past the top")
        self.assertEqual(layout.drag_offset(None, -100, H), 0)

    def test_no_easing_and_no_animation_target(self):
        """The owner's rule: every frame a complete snapshot. A drag follows the finger and then settles in one frame,
        so there is no easing curve, no end-of-animation target and no timing constant for a settle."""
        for name in ("ease", "drag_target", "MOVE_MS"):
            self.assertFalse(hasattr(layout, name), name)

    def test_the_drag_rules_need_no_toolkit(self):
        tree = ast.parse((SHELL.parent / "layout.py").read_text(encoding="utf-8"))
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(mods & {"gi", "gtk"}, mods)


class Fake:
    """Stands in for a toolkit object: records what it is told."""

    def __init__(self, **k):
        self.__dict__.update(k)


class FakeBlanker:
    """Records blank and restore. A blank leaves the screens dark (as the saved file says) until a restore with nothing
    failing; pending() is what blank.Blanker.pending() says. As in the real class, every allowed blank counts in
    `blanks`, also one that finds the screens dark already (and so writes nothing and returns False)."""

    def __init__(self, calls):
        self.calls, self.failed, self.blanks, self.dark = calls, [], 0, False
        self.on_restore = None

    def blank(self):
        self.calls.append("blank")
        self.blanks += 1
        wrote, self.dark = not self.dark, True
        return wrote

    def restore(self):
        self.calls.append("restore")
        if not self.failed:
            self.dark = False

    def pending(self):
        return self.dark or bool(self.failed)


class StandInShell(unittest.TestCase):
    """The shell's own handlers on a window that only records, with a stand-in toolkit. No tests of its own."""
    H = 900
    BORROWED = ("page_kind", "swipe_begin", "swipe_end", "event_ms", "drag_begin", "drag_update", "drag_end",
                "drag_cancel", "dragged", "settle", "set_shade", "lid_changed", "blank_if_closed", "start_lid",
                "lid_closed_now", "lid_lost", "restore", "retry_restore", "restore_again", "end")

    def setUp(self):
        import importlib
        import sys
        from tests.test_duo_native import Toolkit
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
        shell = self.shell = importlib.import_module("opos.native.shell")
        shell.Gtk.EventSequenceState.CLAIMED = "claimed"
        self.timers = []                # (ms, callback, args) handed to GLib.timeout_add
        shell.GLib.timeout_add = lambda ms, fn, *a: self.timers.append((ms, fn, a)) or len(self.timers)
        shell.GLib.idle_add = lambda fn, *a: fn(*a)
        S = shell.Shell
        test = self

        class Win:
            hinge, hand, page, warming, leaving = 42, "right", "lock", False, 0

            def __init__(self):
                self.drag, self.drag_decided, self.drag_start, self.samples, self.owned = None, False, None, [], False
                self.start, self.gone, self.hands, self.quits = None, [], [], 0
                self.lid_closed, self.lid_watcher, self.posture = None, None, "open"
                self.restore_tries, self.restore_timer, self.restore_blanks = 0, None, 0
                self.page_slide, self.sheet_slide = Fake(dy=0.0), Fake(dy=0.0)
                for s in (self.page_slide, self.sheet_slide):
                    s.offset = (lambda s: lambda dy: setattr(s, "dy", float(dy)))(s)
                self.sheet = Fake(revealed=False, target=False)
                self.sheet.get_reveal_child = lambda: self.sheet.revealed
                self.sheet.set_reveal_child = lambda v: setattr(self.sheet, "revealed", v)
                self.sheet.set_can_target = lambda v: setattr(self.sheet, "target", v)
                test.calls = []         # what the blanker was told, in order
                self.blanker = FakeBlanker(test.calls)

            def get_width(self):
                return 1392

            def get_height(self):
                return test.H

            def get_application(self):
                return Fake(quit=lambda: setattr(self, "quits", self.quits + 1))

            def go(self, name):
                self.set_shade(False)
                self.gone.append(name)
                self.page = name

            def set_hand(self, hand):
                self.hands.append(hand)

        for n in self.BORROWED:          # the shell's own methods, on a window that only records
            setattr(Win, n, S.__dict__[n])
        self.win = Win()
        self.g = Fake(t=1000, state=None)
        self.g.get_current_event_time = lambda: self.g.t
        self.g.set_state = lambda st: setattr(self.g, "state", st)
        self.g.get_point = lambda _w: (True,) + self.at

    def touch(self, x, y, moves, end_vx=0.0, end_vy=0.0, pause_ms=0, step_ms=16):
        """One touch: press at (x, y), move through the (dx, dy) list, lift; the swipe reports (end_vx, end_vy)."""
        w, self.at = self.win, (x, y)
        w.swipe_begin(self.g, None)
        w.drag_begin(self.g, x, y)
        dx = dy = 0
        for dx, dy in moves:
            self.g.t += step_ms
            w.drag_update(self.g, dx, dy)
        self.g.t += step_ms + pause_ms
        w.drag_end(self.g, dx, dy)
        w.swipe_end(self.g, end_vx, end_vy)

    def fire_timers(self):
        timers, self.timers[:] = list(self.timers), []
        return [fn(*a) for _ms, fn, a in timers]


class ShellDrags(StandInShell):
    """The shell's drag handlers: claims, following the finger, one action per touch, and a settle in one frame."""

    def test_a_tap_claims_nothing_and_does_nothing(self):
        self.touch(300, 450, [(3, -4), (6, -12)])
        self.assertIsNone(self.g.state)
        self.assertEqual((self.win.owned, self.win.page_slide.dy, self.win.gone, self.timers), (False, 0.0, [], []))

    def test_an_unlock_drag_follows_the_finger_and_unlocks_once_at_lift(self):
        w = self.win
        self.at = (300, 600)
        w.swipe_begin(self.g, None)
        w.drag_begin(self.g, 300, 600)
        for dy in (-13, -100, -200, -380):
            self.g.t += 16
            w.drag_update(self.g, 0, dy)
            self.assertEqual(w.page_slide.dy, dy, "the page is under the finger")
        self.assertEqual(self.g.state, "claimed")
        self.assertEqual(w.gone, [], "nothing is decided while the finger is down")
        self.g.t += 400       # the finger stops, then lifts: no flick, but 380 px is past 40% of 450
        w.drag_end(self.g, 0, -380)
        self.assertEqual((w.gone, w.page_slide.dy, self.timers), (["home"], 0.0, []),
                         "unlocked and back at rest in the same change: nothing left to animate")
        w.swipe_end(self.g, 0, -400)
        self.assertEqual(w.gone, ["home"], "the swipe does not act a second time")

    def test_a_short_unlock_drag_is_put_back_at_once_and_the_swipe_does_not_act_instead(self):
        w = self.win
        self.touch(300, 600, [(0, -13), (0, -100), (0, -150)], end_vy=-400, pause_ms=400)
        self.assertEqual((w.gone, w.page_slide.dy, self.timers), ([], 0.0, []))

    def test_a_short_quick_flick_unlocks(self):
        self.touch(300, 600, [(0, -20), (0, -60)], end_vy=-2000)
        self.assertEqual(self.win.gone, ["home"])

    def test_a_flick_too_short_for_a_drag_is_still_a_swipe(self):
        self.touch(300, 600, [(0, -8)], end_vy=-400)
        self.assertEqual(self.win.gone, ["home"])

    def test_a_sideways_swipe_across_the_hinge_still_moves_the_buttons(self):
        self.touch(1392 / 2 - 60, 400, [(13, 2), (200, 30)], end_vx=-500)
        self.assertEqual((self.win.owned, self.win.hands, self.g.state), (False, ["left"], None))

    def test_the_sheet_follows_down_then_opens_or_goes_back_in_one_frame(self):
        w = self.win
        w.page = "home"
        self.at = (300, 10)
        w.swipe_begin(self.g, None)
        w.drag_begin(self.g, 300, 10)
        self.g.t += 16
        w.drag_update(self.g, 0, 81)
        self.assertTrue(w.sheet.revealed, "shown at once, moved by the finger")
        self.assertAlmostEqual(w.sheet_slide.dy, 81 - self.H, msg="moved 81 px, as far as the finger")
        self.g.t += 400
        w.drag_end(self.g, 0, 81)          # 20%, stopped: back
        w.swipe_end(self.g, 0, 400)
        self.assertEqual((w.sheet.revealed, w.sheet.target, w.sheet_slide.dy), (False, False, 0.0))
        self.touch(300, 10, [(0, 50), (0, 150)], pause_ms=400)   # 37%: opens
        self.assertEqual((w.sheet.revealed, w.sheet.target, w.sheet_slide.dy), (True, True, 0.0))
        self.touch(300, 700, [(0, -13), (0, -100)], pause_ms=400)   # 25% up: stays open
        self.assertEqual((w.sheet.revealed, w.sheet.target, w.sheet_slide.dy), (True, True, 0.0))
        self.touch(300, 700, [(0, -13), (0, -40)], end_vy=-2000)     # a quick flick up closes it
        self.assertEqual((w.sheet.revealed, w.sheet.target, w.sheet_slide.dy), (False, False, 0.0))
        self.assertEqual((w.gone, self.timers), ([], []))

    def test_home_from_the_bottom_edge_of_an_app(self):
        w = self.win
        w.page = "phone"
        self.touch(300, self.H - 5, [(0, -13), (0, -300)], pause_ms=400)
        self.assertEqual(w.gone, ["home"])

    def test_a_cancelled_touch_goes_back(self):
        w = self.win
        self.at = (300, 600)
        w.drag_begin(self.g, 300, 600)
        w.drag_update(self.g, 0, -400)
        w.drag_cancel(self.g, None)
        w.drag_end(self.g, 0, -400)
        self.assertEqual((w.gone, w.page_slide.dy), ([], 0.0))

    def test_nothing_is_dragged_under_the_start_up_cover(self):
        w = self.win
        w.warming = True
        self.touch(300, 600, [(0, -13), (0, -400)], end_vy=-2000)
        self.assertEqual((w.gone, w.page_slide.dy, w.owned, self.g.state), ([], 0.0, False, None))


class ShellLid(StandInShell):
    """lid_changed and friends: closing locks, leaves nothing half moved and blanks later; opening lights the screens."""

    def test_the_first_report_closed_locks(self):
        w = self.win
        w.page = "home"
        w.lid_changed(True)
        self.assertEqual((w.gone, w.page, w.lid_closed, w.posture), (["lock"], "lock", True, "closed"))

    def test_the_first_report_open_changes_no_page_and_lights_the_screens(self):
        w = self.win
        w.lid_changed(False)
        self.assertEqual((w.gone, w.page, w.lid_closed, w.posture, self.calls), ([], "lock", False, "open", ["restore"]))

    def test_closing_later_locks_and_closes_the_sheet(self):
        w = self.win
        w.lid_changed(False)
        w.page = "phone"
        w.set_shade(True)
        self.assertIs(w.lid_changed(True), False, "an idle callback that runs once")
        self.assertEqual(w.gone, ["lock"])
        self.assertEqual((w.sheet.revealed, w.sheet.target, w.posture), (False, False, "closed"))

    def test_closing_during_a_drag_puts_the_page_back_and_the_lifted_finger_does_nothing(self):
        w = self.win
        w.lid_changed(False)
        w.page = "phone"
        self.at = (300, self.H - 5)
        w.swipe_begin(self.g, None)
        w.drag_begin(self.g, 300, self.H - 5)
        for dy in (-13, -200, -300):
            self.g.t += 16
            w.drag_update(self.g, 0, dy)
        self.assertEqual(w.page_slide.dy, -300)
        w.lid_changed(True)
        self.assertEqual((w.page_slide.dy, w.drag, w.gone), (0.0, None, ["lock"]))
        self.g.t += 16
        w.drag_update(self.g, 0, -400)
        w.drag_end(self.g, 0, -400)
        w.swipe_end(self.g, 0, -2000)      # on the lock screen a swipe up would unlock: it must not
        self.assertEqual((w.gone, w.page, w.page_slide.dy), (["lock"], "lock", 0.0))

    def test_closing_during_a_sheet_drag_hides_the_sheet(self):
        w = self.win
        w.lid_changed(False)
        w.page = "home"
        self.at = (300, 10)
        w.swipe_begin(self.g, None)
        w.drag_begin(self.g, 300, 10)
        self.g.t += 16
        w.drag_update(self.g, 0, 200)
        self.assertTrue(w.sheet.revealed)
        w.lid_changed(True)
        self.assertEqual((w.sheet.revealed, w.sheet.target, w.sheet_slide.dy, w.gone), (False, False, 0.0, ["lock"]))

    def test_closing_blanks_after_the_lock_if_still_closed(self):
        w = self.win
        w.lid_changed(False)
        self.calls.clear()
        w.lid_changed(True)
        self.assertEqual(self.calls, [], "not at once: the lock screen is drawn first")
        self.assertEqual([ms for ms, _f, _a in self.timers], [self.shell.BLANK_MS])
        self.assertEqual(self.fire_timers(), [False], "the timer runs once")
        self.assertEqual(self.calls, ["blank"])

    def test_a_blank_that_fires_after_the_lid_opened_again_does_nothing(self):
        w = self.win
        w.lid_changed(False)
        w.lid_changed(True)
        w.lid_changed(False)
        self.calls.clear()
        self.fire_timers()
        self.assertEqual(self.calls, [])

    def test_opening_restores_at_once(self):
        w = self.win
        w.lid_changed(False)
        w.lid_changed(True)
        self.fire_timers()
        self.calls.clear()
        w.lid_changed(False)
        self.assertEqual((self.calls, self.timers), (["restore"], []))

    def fire_one(self):
        """Run the one waiting timer; (its delay, what it returned)."""
        self.assertEqual(len(self.timers), 1, "one try waits at a time")
        ms, fn, a = self.timers.pop(0)
        return ms, fn(*a)

    def test_a_restore_that_could_not_write_is_tried_again_with_backoff_for_as_long_as_the_lid_stays_open(self):
        w = self.win
        w.blanker.failed = ["panel0-backlight: busy"]
        w.lid_changed(False)
        delays = [self.fire_one() for _ in range(40)]
        self.assertEqual([ms for ms, _r in delays], [1000, 2000, 4000] + [5000] * 37, "then every 5 s, with no last try")
        self.assertEqual({r for _ms, r in delays}, {False}, "each try is a one-shot timer: no busy loop")
        self.assertEqual(self.calls.count("restore"), 41)
        w.blanker.failed = []           # the panel took the write at last
        self.fire_one()
        self.assertEqual((self.timers, w.blanker.pending()), ([], False), "lit: no more tries")

    def test_the_tries_stop_when_the_lid_closes_or_someone_darkens_the_screens_on_purpose(self):
        w = self.win
        w.blanker.failed = ["x"]
        w.lid_changed(False)
        w.lid_closed = True
        self.calls.clear()
        self.assertEqual(self.fire_one(), (1000, False))
        self.assertEqual((self.calls, self.timers), ([], []), "closed again meanwhile: dark is right")
        w.lid_changed(False)
        w.blanker.blank()              # the daemon's blank while the lid is unknown, say
        self.calls.clear()
        self.fire_one()
        self.assertEqual((self.calls, self.timers), ([], []), "a newer blank wins until the next restore")

    def test_a_restore_while_a_try_waits_puts_up_no_second_timer(self):
        w = self.win
        w.blanker.failed = ["x"]
        w.lid_changed(False)
        w.lid_changed(False)
        w.restore()
        self.assertEqual(len(self.timers), 1)

    def test_with_the_real_blanker_a_panel_that_fails_for_a_minute_is_lit_once_it_takes_the_write(self):
        from opos.native import blank
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run = root / "run"
            run.mkdir(mode=0o700)
            for p_, v in zip(blank.PANELS, (90, 200)):
                (root / "sys/class/backlight" / p_).mkdir(parents=True)
                (root / "sys/class/backlight" / p_ / "brightness").write_text("%d\n" % v)
            w = self.win
            w.blanker = blank.Blanker(root=str(root), run_dir=str(run), say=lambda t: None)
            w.lid_changed(True)
            self.fire_one()                         # the blank
            panel0 = root / "sys/class/backlight/panel0-backlight/brightness"
            panel0.unlink()
            panel0.mkdir()                          # EISDIR stands in for a write the panel refuses for a while
            w.lid_changed(False)
            for _ in range(15):                     # about a minute of failed tries: 1+2+4+12*5 s
                self.fire_one()
            self.assertTrue((run / blank.SAVED_NAME).exists(), "still dark, still saved, still trying")
            panel0.rmdir()
            panel0.write_text("0\n")
            self.fire_one()
            self.assertEqual((panel0.read_text().strip(), (run / blank.SAVED_NAME).exists(), self.timers), ("90", False, []))

    def real_tree(self, d):
        """Two panels at 90 and 200 and a private runtime folder under d; the window gets a real Blanker on them, with the
        shell's own guard."""
        from opos.native import blank
        root = Path(d)
        run = root / "run"
        run.mkdir(mode=0o700)
        for p_, v in zip(blank.PANELS, (90, 200)):
            (root / "sys/class/backlight" / p_).mkdir(parents=True)
            (root / "sys/class/backlight" / p_ / "brightness").write_text("%d\n" % v)
        w = self.win
        w.blanker = blank.Blanker(root=str(root), run_dir=str(run), say=lambda t: None, may_blank=lambda: w.lid_closed is not False)
        return [root / "sys/class/backlight" / p_ / "brightness" for p_ in blank.PANELS], run / blank.SAVED_NAME

    def test_a_daemon_restore_that_fails_is_tried_again(self):
        """R3: the lid is unknown (no watcher), the daemon blanks (3), a panel refuses the write, the daemon restores (0).
        That restore goes through Shell.restore, so it is tried again until the panel takes the write."""
        with tempfile.TemporaryDirectory() as d:
            (p0, p1), saved = self.real_tree(d)
            w = self.win
            self.start_with(None)               # wires on_restore; the lid reads nothing
            w.lid_closed = None
            w.blanker.set_mode(3)
            self.assertEqual((p0.read_text().strip(), p1.read_text().strip()), ("0", "0"))
            p0.unlink()
            p0.mkdir()
            w.blanker.set_mode(0)
            self.assertTrue(saved.exists())
            self.assertEqual(len(self.timers), 1, "a try is waiting")
            p0.rmdir()
            p0.write_text("0\n")
            self.fire_one()
            self.assertEqual((p0.read_text().strip(), p1.read_text().strip(), saved.exists(), self.timers), ("90", "200", False, []))

    def test_a_daemon_blank_on_screens_already_dark_stops_a_pending_retry(self):
        """R7: a restore is pending (both panels refuse the write), then the daemon blanks while the lid is unknown. Both
        panels already read 0, so that blank writes nothing, but dark is what was asked: the retry must not relight."""
        with tempfile.TemporaryDirectory() as d:
            (p0, p1), saved = self.real_tree(d)
            w = self.win
            w.lid_closed = None
            w.blanker.set_mode(3)
            for p_ in (p0, p1):
                p_.unlink()
                p_.mkdir()
            w.lid_lost()                        # lights them now: both refuse, so a try waits
            self.assertEqual(len(self.timers), 1)
            w.blanker.set_mode(3)               # the daemon, half a minute later: the lid is closed, it says
            for p_ in (p0, p1):
                p_.rmdir()
                p_.write_text("0\n")
            self.fire_one()
            self.assertEqual((p0.read_text().strip(), p1.read_text().strip(), saved.exists(), self.timers), ("0", "0", True, []),
                             "dark as the daemon asked; the next open or restore lights them")

    def start_with(self, state):
        """start_lid with the lid reading `state`, no bus and no thread; returns the blanker handed to own()."""
        from unittest import mock
        shell, owned = self.shell, []
        with tempfile.TemporaryDirectory() as d:
            node = Path(d) / "event5"
            node.write_bytes(b"")
            with mock.patch.object(shell.posture, "find_lid", lambda *a: str(node)), \
                    mock.patch.object(shell.posture, "lid_state", lambda fd: state), \
                    mock.patch.object(shell.posture, "LidWatcher", lambda *a: Fake(start=lambda: "watcher")), \
                    mock.patch.object(shell.displayconfig, "own", lambda b, say: owned.append(b)):
                self.win.start_lid()
        return owned

    def test_start_lid_routes_the_daemons_restore_through_the_shell(self):
        w = self.win
        self.assertEqual(self.start_with(True), [w.blanker])
        self.assertEqual(w.blanker.on_restore, w.restore)

    def test_a_lost_lid_signal_makes_the_lid_unknown_and_lights_the_screens(self):
        w = self.win
        w.lid_changed(True)
        self.fire_timers()                      # dark
        self.calls.clear()
        w.blanker.failed = ["x"]
        self.assertIs(w.lid_lost(), False)
        self.assertIsNone(w.lid_closed)
        self.assertEqual(self.calls, ["restore"], "no lid signal means no opening will ever be seen: light them now")
        self.assertEqual(self.fire_one(), (1000, False))
        self.assertEqual(self.calls, ["restore", "restore"], "and keep trying")

    def test_sigterm_lights_the_screens_and_ends_as_143(self):
        w = self.win
        self.assertIs(w.end(), False)
        self.assertEqual((self.calls, w.leaving, w.quits), (["restore"], 143, 1))

    def start(self, state, path=True):
        """start_lid with the lid reading `state` (True closed, False open, None unreadable), no bus and no thread."""
        from unittest import mock
        shell, owned = self.shell, []
        with tempfile.TemporaryDirectory() as d:
            node = Path(d) / "event5"
            node.write_bytes(b"")
            with mock.patch.object(shell.posture, "find_lid", lambda *a: str(node) if path else None), \
                    mock.patch.object(shell.posture, "lid_state", lambda fd: state), \
                    mock.patch.object(shell.posture, "LidWatcher", lambda *a: Fake(start=lambda: "watcher")), \
                    mock.patch.object(shell.displayconfig, "own", lambda b, say: owned.append(b)):
                self.win.start_lid()
        self.assertEqual((owned, self.win.lid_watcher), ([self.win.blanker], "watcher"))
        return self.calls

    def test_start_restores_a_phone_left_dark_unless_the_lid_is_closed(self):
        self.assertEqual(self.start(False), ["restore"])
        self.calls.clear()
        self.assertEqual(self.start(True), [], "closed now: stay dark")
        self.assertEqual(self.start(None), ["restore"], "unreadable means open")
        self.calls.clear()
        self.assertEqual(self.start(True, path=False), ["restore"], "no lid device means open")


class TheDaemonCannotDarkenAnOpenPhone(unittest.TestCase):
    """The shell's blanker refuses the lid daemon's blank while its own lid switch reads open (a late or repeated 3
    after the lid opened would otherwise leave both screens dark), and obeys it when the lid is closed or unknown."""

    def test_the_guard(self):
        import sys
        from unittest import mock
        from opos.native import blank
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run = root / "run"
            run.mkdir(mode=0o700)
            for p, v in zip(blank.PANELS, (90, 200)):
                (root / "sys/class/backlight" / p).mkdir(parents=True)
                (root / "sys/class/backlight" / p / "brightness").write_text("%d\n" % v)
            state = {"lid": False}
            b = blank.Blanker(root=str(root), run_dir=str(run), say=lambda t: None,
                              may_blank=lambda: state["lid"] is not False)
            b.set_mode(3)
            self.assertFalse((run / blank.SAVED_NAME).exists(), "open: refused")
            state["lid"] = None
            b.set_mode(3)
            self.assertTrue((run / blank.SAVED_NAME).exists(), "unknown: the daemon is obeyed")
            b.set_mode(0)
            state["lid"] = True
            b.set_mode(3)
            self.assertTrue((run / blank.SAVED_NAME).exists(), "closed: obeyed")
            state["lid"] = False
            b.set_mode(0)
            self.assertEqual((root / "sys/class/backlight/panel0-backlight/brightness").read_text().strip(), "90",
                             "a restore is never refused")
        self.assertIn("may_blank=lambda: self.lid_closed is not False", SHELL.read_text(encoding="utf-8"))
        del sys, mock


class LidSource(unittest.TestCase):
    def test_no_debug_paths_and_no_grab(self):
        for path in (SHELL, SHELL.parent / "posture.py", SHELL.parent / "blank.py", SHELL.parent / "displayconfig.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("/sys/kernel/debug", text)
                self.assertNotIn("EVIOCGRAB", text)
                self.assertNotIn("bl_power", text)

    def test_the_lid_is_watched_only_outside_the_checking_modes_and_every_end_lights_the_screens(self):
        text = SHELL.read_text(encoding="utf-8")
        self.assertIn("if not probe and not shots:", text)
        self.assertIn("win.start_lid()", text)
        self.assertIn("GLib.idle_add(self.lid_changed", text)
        self.assertIn("GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, win.end)", text)
        main = text[text.index("def main("):]
        self.assertLess(main.index("finally:"), main.index("win.blanker.restore()"))


class Hand(unittest.TestCase):
    def test_the_buttons_follow_the_hand_and_nothing_else_changes(self):
        self.assertEqual(layout.pane_order("right"), ("read", "act"))
        self.assertEqual(layout.pane_order("left"), ("act", "read"))
        self.assertEqual(layout.pane_order("nonsense"), ("read", "act"))

    def test_saved_and_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "opos" / "shell.json"
            self.assertEqual(layout.load_hand(path, {}), "right", "no file yet")
            self.assertTrue(layout.save_hand("left", path))
            self.assertEqual(layout.load_hand(path, {}), "left")
            path.write_text('{"hand": "left", "other": 1}', encoding="utf-8")
            self.assertTrue(layout.save_hand("right", path))
            self.assertIn('"other": 1', path.read_text(encoding="utf-8"), "other settings are kept")
            self.assertEqual(layout.load_hand(path, {}), "right")

    def test_bad_values_and_bad_files_mean_right(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "shell.json"
            self.assertFalse(layout.save_hand("both", path))
            self.assertFalse(path.exists())
            for text in ("", "not json", "[]", '{"hand": "up"}', '{"hand": 3}'):
                path.write_text(text, encoding="utf-8")
                with self.subTest(text=text):
                    self.assertEqual(layout.load_hand(path, {}), "right")

    def test_the_environment_overrides_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "shell.json"
            layout.save_hand("right", path)
            self.assertEqual(layout.load_hand(path, {"OPOS_HAND": "left"}), "left")
            self.assertEqual(layout.load_hand(path, {"OPOS_HAND": "sideways"}), "right")

    def test_the_settings_file_lives_under_the_users_config_folder(self):
        self.assertEqual(layout.settings_path({"XDG_CONFIG_HOME": "/x"}), Path("/x") / "opos" / "shell.json")
        self.assertEqual(layout.settings_path({"HOME": "/h"}), Path("/h") / ".config" / "opos" / "shell.json")


def source(name):
    return (NATIVE / name).read_text(encoding="utf-8")


class EveryFrameIsASnapshot(unittest.TestCase):
    """Animation is a skin for later: by default nothing moves, so every frame is complete and accurate."""

    def test_animation_is_a_constant_and_it_is_off(self):
        tree = ast.parse(source("ui.py"))
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
                  if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)}
        self.assertIs(consts["ANIMATE"], False)

    def test_no_css_transition_or_animation(self):
        text = source("ui.py")
        css = text[text.index('CSS = """'):text.index('"""', text.index('CSS = """') + 9)]
        for word in ("transition", "animation", "@keyframes"):
            self.assertNotIn(word, css)

    def test_every_stack_and_the_sheet_use_no_transition_unless_animation_is_on(self):
        shell = source("shell.py")
        self.assertNotIn("transition_duration=MOVE_MS,", shell, "a duration must depend on ANIMATE")
        self.assertEqual(shell.count("MOVE_MS if ANIMATE else 0"), 3, "both stacks and the battery sheet")
        self.assertIn("transition_type=Gtk.StackTransitionType.NONE", source("ui.py"), "the keyboard layers never slide")

    def test_every_reading_pane_scrolls_by_finger_whatever_animate_says(self):
        """kinetic_scrolling=False turns off GTK 4's touch drag and pan on a ScrolledWindow, so a list taller than the
        pane (Contacts, Notes, Call log, Settings) could not be moved by touch. It is always on, never tied to ANIMATE."""
        tree = ast.parse(source("ui.py"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reading")
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "ScrolledWindow"]
        self.assertEqual(len(calls), 1)
        kinetic = {k.arg: k.value for k in calls[0].keywords}["kinetic_scrolling"]
        self.assertIsInstance(kinetic, ast.Constant)
        self.assertIs(kinetic.value, True)
        self.assertEqual(source("ui.py").count("ScrolledWindow("), 1, "every reading pane is made by reading()")
        self.assertNotIn("kinetic_scrolling=ANIMATE", source("ui.py"))

    def test_the_start_up_walk_plays_nothing_under_the_cover(self):
        shell = source("shell.py")
        walk = shell[shell.index("    def warm(self):"):shell.index("    def leave(self")]
        self.assertNotIn("SLIDE", walk)
        self.assertNotIn("StackTransitionType", walk)
        self.assertIn("self.warming = False", walk)

    def test_go_while_warming_waits_instead_of_leaving_the_page_and_the_stacks_apart(self):
        shell = source("shell.py")
        go = shell[shell.index("    def go(self, name):"):shell.index("    def warm(self):")]
        self.assertLess(go.index("if self.warming:"), go.index("self.page = name"), "the page is not changed under the cover")
        self.assertIn("self.wanted = name", go)
        self.assertIn("self.go(wanted)", shell, "and it is made the moment the cover goes")


class DragsSettleInOneFrame(unittest.TestCase):
    """A drag follows the finger (direct manipulation); letting go never starts an animation."""

    def test_no_frame_clock_work_outside_the_probe_and_no_easing(self):
        shell = source("shell.py")
        self.assertEqual(shell.count("add_tick_callback("), 1, "only the --probe measurement uses the frame clock")
        self.assertIn("if probe:", shell[:shell.index("add_tick_callback(") + 1][-200:])
        for pattern in (r"\bease\(", r"\banimate\(", r"remove_tick_callback", r"settle_now", r"\bmotion\b"):
            self.assertIsNone(re.search(pattern, shell), pattern)
        settle = shell[shell.index("    def settle("):shell.index("    # --- the lid")]
        self.assertNotIn("timeout_add", settle)


class NoAssistants(unittest.TestCase):
    """The owner: finish the phone OS first, AI later. Nothing in the shell talks to a model or holds a key."""

    def test_the_module_the_screen_and_the_hook_are_gone(self):
        self.assertFalse((NATIVE / "assistants.py").exists())
        for name in ("shell.py", "screens.py", "ui.py", "logic.py"):
            text = source(name)
            for word in ("assistants", "OPOS_ASK", "class Ask", "api_key", "X-api-key", "Authorization"):
                self.assertNotIn(word, text, name)

    def test_assistants_are_noted_as_deferred_in_the_docs(self):
        doc = (Path(__file__).resolve().parents[1] / "packaging" / "duo-os" / "NATIVE.md").read_text(encoding="utf-8")
        self.assertIn("Assistants are deferred", doc)


class TheScreensStayTrue(unittest.TestCase):
    """What the screens do about late replies, refusals and drafts, read from the source (GTK widgets cannot be built
    without a display; the rules themselves are tested in test_native_logic)."""

    def test_a_row_tap_asks_the_newest_handler(self):
        self.assertIn("box.rowset.click(i)", source("ui.py"))

    def test_every_app_answer_is_ordered_and_a_write_that_lost_the_race_is_drawn_again(self):
        screens = source("screens.py")
        self.assertIn("fresh = self.seq.accept(n, floor=wrote and self.confirmed(data))", screens)
        self.assertIn("if wrote and not fresh and self.confirmed(data):", screens)

    def test_the_polls_clock_answers_go_through_the_same_order(self):
        shell = source("shell.py")
        self.assertIn("m = clock.seq.issue()", shell)
        self.assertIn("clock.deliver(n, data)", shell)

    def test_a_failed_poll_keeps_the_snapshot(self):
        shell = source("shell.py")
        self.assertNotIn("OPOS is starting", shell)
        polled = shell[shell.index("    def polled("):shell.index("    def clock_seen(")]
        self.assertIn("self.link.failed()", polled)
        self.assertNotIn("self.snap =", polled)

    def test_the_draft_the_contact_and_the_alarm_are_cleared_only_by_the_servers_answer(self):
        screens = source("screens.py")
        messages = screens[screens.index("class Messages("):screens.index("class Clock(")]
        start = messages[messages.index("    def send_typed("):messages.index("    def sent(")]
        self.assertNotIn("self.typed.text = \"\"", start)
        contacts = screens[screens.index("class Contacts("):screens.index("class Notes(")]
        save = contacts[contacts.index("    def save("):contacts.index("    def person(")]
        self.assertLess(save.index("def done("), save.index("e.text = \"\""))
        self.assertNotIn("self.saving", contacts, "no flag shared between one answer and the next")
        clock = screens[screens.index("class Clock("):screens.index("class Contacts(")]
        add = clock[clock.index("    def add_alarm("):clock.index("    def choose(")]
        self.assertLess(add.index("def done("), add.index('self.digits = ""'))

    def test_notes_close_and_delete_wait_for_the_server(self):
        screens = source("screens.py")
        notes = screens[screens.index("class Notes("):screens.index("class Calculator(")]
        self.assertNotIn("self.ident = None\n        self.mode()\n        self.call", notes)
        self.assertIn("if leave and self.ident == ident:", notes)
        self.assertIn('self.want in ("add", data.get("id"))', notes, "only the note asked for last becomes the open one")

    def test_practice_is_said_on_messages_home_and_phone(self):
        screens = source("screens.py")
        self.assertIn("logic.messages_line(snap, self.outcome)", screens)
        self.assertIn('snap.get("home_note")', screens)
        self.assertEqual(screens.count("logic.call_words(snap)"), 2, "the Phone screen and its preview on Home")

    def test_a_clock_nobody_set_is_warned_about(self):
        screens = source("screens.py")
        self.assertIn('data.get("clock_ok") is not False', screens)
        self.assertIn("logic.CLOCK_UNSET", screens)


class ShellModule(unittest.TestCase):
    def test_the_source_parses_and_uses_the_shared_rules(self):
        tree = ast.parse(SHELL.read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "opos.native.layout" for a in n.names}
        self.assertLessEqual({"hinge_px", "swipe_action", "pane_order", "load_hand", "save_hand", "drag_kind",
                              "drag_progress", "drag_release"}, imported)

    def test_the_module_imports_where_gtk_is_installed(self):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
        except (ImportError, ValueError):
            self.skipTest("GTK 4 or PyGObject is not installed here")
        from opos.native import shell
        self.assertEqual(shell.hinge_px({"OPOS_SPAN": "675,42"}), 42)
        self.assertTrue(callable(shell.main))

    def test_every_page_builds_its_class_where_gtk_is_installed(self):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
        except (ImportError, ValueError):
            self.skipTest("GTK 4 or PyGObject is not installed here")
        from opos.native import screens
        self.assertEqual([c.name for c in screens.PAGES],
                         ["lock", "home", "phone", "messages", "clock", "contacts", "notes", "calc", "calls", "settings"])


if __name__ == "__main__":
    unittest.main()
